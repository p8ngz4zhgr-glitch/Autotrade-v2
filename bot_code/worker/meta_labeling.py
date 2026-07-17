# ═══════════════════════════════════════════════════════════
# META-LABELING FILTER — worker/meta_labeling.py
# ═══════════════════════════════════════════════════════════
"""
Lớp lọc "có nên vào lệnh này không", tách biệt khỏi việc đoán HƯỚNG lệnh.

Tinh thần Triple-Barrier / Meta-labeling (López de Prado): engine.py và hội
đồng 12-agent đã trả lời "nên LONG hay SHORT". Nhãn thắng/thua thực tế của
mỗi lệnh trong TradeJournal (WIN nếu pnl_pct>0, LOSS nếu ngược lại) CHÍNH LÀ
kết quả chạm rào TP/SL/breakeven/reversal đang chạy live — không cần dựng
lại "triple barrier" từ giá thô, vì bot đã tự làm việc đó rồi mỗi khi đóng
lệnh. Ở đây chỉ cần: với đúng bối cảnh (confidence, p_win, regime, OI,
funding...) tại lúc vào lệnh, LỊCH SỬ THỰC TẾ cho thấy loại setup này có hay
thắng không? Nếu không — lọc bớt, dù nhìn "đẹp" trên giấy.

QUAN TRỌNG — vì sao KHÔNG lưu model xuống file:
Free web service trên Render có filesystem tạm thời, mất sạch mỗi lần
restart/redeploy (xem ghi chú hạ tầng đã trao đổi). Nên module này RETRAIN
LẠI TỪ DATABASE mỗi khi cần (cache RAM 1 giờ) thay vì pickle model ra đĩa.
Với vài chục–vài trăm dòng dữ liệu, Logistic Regression fit trong vài chục ms
nên retrain thường xuyên không phải vấn đề hiệu năng.

Fail-open TUYỆT ĐỐI: thiếu scikit-learn, thiếu dữ liệu, hoặc bất kỳ lỗi nào
khác đều trả về allow=True — không bao giờ để lớp lọc này làm treo hoặc chặn
nhầm toàn bộ hệ thống vì lý do hạ tầng.
"""
import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger("MetaLabel")

MIN_SAMPLES_TO_TRUST = 30   # Chưa đủ mẫu -> chỉ quan sát (allow=True), không chặn
REJECT_BELOW_P_WIN   = 0.35  # Chỉ chặn khi meta-model rõ ràng bi quan


def extract_features(ctx: dict) -> list:
    """Chuẩn hoá 1 dict bối cảnh (entry_features) thành vector số cố định 8 chiều."""
    direction  = ctx.get("direction", "LONG")
    hmm_regime = (ctx.get("hmm") or {}).get("regime", "SIDEWAYS")
    hmm_dir_agree = 1.0 if (
        (direction == "LONG" and hmm_regime == "UPTREND") or
        (direction == "SHORT" and hmm_regime == "DOWNTREND")
    ) else 0.0

    oi_signal = ctx.get("oi_signal", "NEUTRAL")
    oi_bias_raw = {"LONG_BUILD": 1, "SHORT_SQUEEZE": 1,
                   "SHORT_BUILD": -1, "LONG_LIQ": -1}.get(oi_signal, 0)
    oi_bias = -oi_bias_raw if direction == "SHORT" else oi_bias_raw

    bayes = ctx.get("bayes_ev") or {}
    funding = float(ctx.get("funding", 0) or 0)

    return [
        float(ctx.get("confidence", 70) or 70) / 100.0,
        float(bayes.get("p_win", 50) or 50) / 100.0,
        float(bayes.get("ev_ratio", 0.2) or 0.2),
        float(bayes.get("likelihood", 1.0) or 1.0),
        min(float(ctx.get("rr_ratio", 1.5) or 1.5), 5.0) / 5.0,
        hmm_dir_agree,
        float(oi_bias),
        min(abs(funding), 0.2) / 0.2,
    ]


class MetaLabelFilter:
    _cache = {"model": None, "trained_at": None, "n_samples": 0}
    _CACHE_TTL = timedelta(hours=1)

    @classmethod
    def _get_model(cls, db):
        now = datetime.utcnow()
        if cls._cache["trained_at"] and (now - cls._cache["trained_at"]) < cls._CACHE_TTL:
            return cls._cache["model"], cls._cache["n_samples"]

        try:
            from sklearn.linear_model import LogisticRegression
        except ImportError:
            log.warning("⚠️ [META] Chưa cài scikit-learn -> bỏ qua meta-filter (không chặn lệnh nào).")
            cls._cache = {"model": None, "trained_at": now, "n_samples": 0}
            return None, 0

        try:
            from core_api.models import TradeJournal

            rows = (
                db.query(TradeJournal)
                .filter(TradeJournal.entry_features.isnot(None))
                .order_by(TradeJournal.timestamp.desc())
                .limit(1000)
                .all()
            )

            X, y = [], []
            for r in rows:
                try:
                    ctx = json.loads(r.entry_features)
                    X.append(extract_features(ctx))
                    y.append(1 if (r.pnl_pct or 0) > 0 else 0)
                except Exception:
                    continue

            n = len(X)
            if n < MIN_SAMPLES_TO_TRUST or len(set(y)) < 2:
                cls._cache = {"model": None, "trained_at": now, "n_samples": n}
                return None, n

            model = LogisticRegression(C=0.5, class_weight="balanced", max_iter=500)
            model.fit(X, y)
            cls._cache = {"model": model, "trained_at": now, "n_samples": n}
            log.info("🧠 [META] Đã retrain trên %d lệnh có nhãn (%d thắng).", n, sum(y))
            return model, n
        except Exception as e:
            log.warning("⚠️ [META] Lỗi retrain, bỏ qua không chặn: %s", e)
            cls._cache = {"model": None, "trained_at": now, "n_samples": 0}
            return None, 0

    @classmethod
    def evaluate(cls, db, entry_features: dict) -> dict:
        """
        Trả về {"allow": bool, "meta_p_win": float|None, "n_samples": int, "reason": str}.
        Fail-open ở MỌI nhánh lỗi/thiếu dữ liệu.
        """
        model, n = cls._get_model(db)
        if model is None:
            return {
                "allow": True, "meta_p_win": None, "n_samples": n,
                "reason": f"Chưa đủ dữ liệu để tin cậy ({n}/{MIN_SAMPLES_TO_TRUST} lệnh có nhãn)",
            }
        try:
            x = extract_features(entry_features)
            meta_p = float(model.predict_proba([x])[0][1])
            allow = meta_p >= REJECT_BELOW_P_WIN
            return {
                "allow": allow, "meta_p_win": round(meta_p * 100, 1), "n_samples": n,
                "reason": "OK" if allow else f"Meta-model: P(win)={meta_p*100:.1f}% < {REJECT_BELOW_P_WIN*100:.0f}%",
            }
        except Exception as e:
            log.warning("⚠️ [META] Lỗi predict, bỏ qua không chặn: %s", e)
            return {"allow": True, "meta_p_win": None, "n_samples": n, "reason": f"Lỗi predict: {e}"}
