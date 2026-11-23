# ═══════════════════════════════════════════════════════════════════════════════
# PURE TRADERSZ PRICE ACTION, LIQUIDITY & DEVIATION (SFP) ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
# Triết lý giao dịch thuần Naked Chart của TraderSZ:
# 1. Context (Dealing Range & HTF Opens):
#    - Macro Opens: Year Open (YO), Monthly Open (MO), Weekly Open (WO), Daily Open (DO)
#    - Key Ranges: Previous Day High/Low (PDH/PDL), Monday High/Low (Monday Range),
#                  Previous Week High/Low (PWH/PWL), Asian Range (00:00-08:00 UTC)
#    - Range High (RH), Range Low (RL), 50% Equilibrium (EQ = (RH + RL) / 2)
# 2. No-Trade Zone vs Edge Zones:
#    - Đứng ngoài (WAIT) khi giá lơ lửng ở vùng giữa quanh 50% EQ.
#    - Vùng Premium (> 50% EQ, sát RH): CHỈ canh SHORT khi có Deviation/SFP.
#    - Vùng Discount (< 50% EQ, sát RL): CHỈ canh LONG khi có Deviation/SFP.
# 3. Quy luật Deviation / SFP (Swing Failure Pattern) & "Return to Range":
#    - Không bao giờ FOMO đánh Breakout khi giá vượt RH hoặc thủng RL.
#    - Chờ giá quét thanh khoản ra ngoài biên Range (Deviation/SFP), sau đó nến
#      (15m, 1H, 4H hoặc D1) rút râu / đóng cửa quay trở lại BÊN TRONG Range.
# 4. Entry, Stop Loss & Take Profit (Range-to-Range):
#    - Entry: Ngay khi nến xác nhận Return to Range (hoặc retest biên vừa quét).
#    - SL: Đặt ngay phía trên đỉnh râu Deviation (SHORT) hoặc dưới đáy râu Deviation (LONG).
#    - TP1: Luôn là trục giữa Range — 50% Equilibrium (EQ) (chốt 50%, dời SL về hòa vốn).
#    - TP2: Biên đối diện của Range (Range Low nếu Short / Range High nếu Long).
# ═══════════════════════════════════════════════════════════════════════════════

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple


class TradersszFramework:
    """Pure TraderSZ Price Action Engine (Naked Chart — Zero Lagging Indicators)."""

    @staticmethod
    def detect_session_context(utcnow: Optional[datetime] = None) -> Dict[str, Any]:
        """Xác định phiên giao dịch UTC hiện tại (Asia / London / New York / Dead Zone)."""
        if utcnow is None:
            utcnow = datetime.now(timezone.utc)
        hour_utc = utcnow.hour + utcnow.minute / 60.0

        if 0.0 <= hour_utc < 8.0:
            return {
                "session": "ASIA",
                "is_killzone": False,
                "allow_trades": True,
                "preferred_mode": "ASIA_RANGE_BUILDING",
                "desc": "Phiên Á (00:00–08:00 UTC) — Hình thành biên Range Á, canh SFP tại PDH/PDL & Monday Range",
            }
        elif 8.0 <= hour_utc < 13.0:
            return {
                "session": "LONDON_OPEN",
                "is_killzone": True,
                "allow_trades": True,
                "preferred_mode": "LONDON_DEVIATION_SFP",
                "desc": "London Killzone (08:00–13:00 UTC) — Thời điểm vàng quét biên Range / Asia High-Low & Return to Range",
            }
        elif 13.0 <= hour_utc < 21.0:
            return {
                "session": "NEW_YORK_OPEN",
                "is_killzone": True,
                "allow_trades": True,
                "preferred_mode": "NY_RANGE_TO_RANGE",
                "desc": "New York Killzone (13:00–21:00 UTC) — Thanh khoản cao nhất, đẩy giá từ biên này sang biên kia Range",
            }
        else:
            return {
                "session": "DEAD_ZONE",
                "is_killzone": False,
                "allow_trades": True,  # Crypto giao dịch 24/7, chỉ không cộng bonus Killzone
                "preferred_mode": "LOW_LIQUIDITY_FILTER",
                "desc": "Late NY / Rollover (21:00–24:00 UTC) — Thanh khoản mỏng, chỉ chọn SFP khung lớn R:R cao",
            }

    @staticmethod
    def _ts_to_utc(ts_ms: int) -> datetime:
        if ts_ms > 1e11:
            return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(float(ts_ms), tz=timezone.utc)

    @staticmethod
    def _find_active_swing_range(
        h4_klines: Optional[Dict[str, List[float]]],
        d1_klines: Optional[Dict[str, List[float]]],
        current_price: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Tự động xác định Active Swing Dealing Range từ đỉnh/đáy cấu trúc rõ nhất trên 4H (tối đa 60 nến đã đóng)
        hoặc 1D (30 nến đã đóng) khi giá thoát khỏi PDH/PDL hoặc bước vào con sóng mới.
        """
        src = None
        if h4_klines and len(h4_klines.get("close", [])) >= 18:
            src = h4_klines
            label = "Active Swing Range (4H)"
        elif d1_klines and len(d1_klines.get("close", [])) >= 14:
            src = d1_klines
            label = "Active Swing Range (1D)"
        if not src or current_price <= 0:
            return None

        # Chỉ dùng nến đã đóng [:-1]
        highs = src["high"][:-1][-60:]
        lows = src["low"][:-1][-60:]
        if len(highs) < 10:
            return None

        sh = float(max(highs))
        sl = float(min(lows))
        if sh <= sl or sl <= 0:
            return None
        width_pct = (sh - sl) / sl * 100.0
        if width_pct < 2.0:
            return None
        # Chỉ hợp lệ làm Active Range nếu giá hiện tại còn nằm trong hoặc quanh ±5% biên của nó
        if not (sl * 0.95 <= current_price <= sh * 1.05):
            return None

        return {
            "name": "SWING_RANGE",
            "label": label,
            "range_high": round(sh, 4),
            "range_low": round(sl, 4),
            "eq_50": round((sh + sl) / 2.0, 4),
            "width_pct": round(width_pct, 2),
            "weight": 3,
        }

    @staticmethod
    def _find_intraday_dealing_range(
        m15_klines: Optional[Dict[str, List[float]]],
        h1_klines: Optional[Dict[str, List[float]]],
        current_price: float,
        far_from_daily: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Nhận diện Range Nội Ngày (Intraday Dealing Range) khi giá đã tăng/giảm xa trong ngày
        rồi chững lại đi ngang tích lũy tạo biên mới (triết lý 90% đi ngang của TraderSZ).

        Dùng nến ĐÃ ĐÓNG ([:-1]) trên 15m (12..32 nến = 3h–8h) hoặc fallback 1H (6..16 nến):
        - Biên độ đủ rộng (>= 0.85% và <= 9.0%) để đảm bảo SL >= 0.55% và đạt R:R >= 1:1.5 tới TP2.
        - Phải có dao động 2 chiều thực sự quanh 50% EQ (>= 2 lần chạm vùng 35% trên & dưới,
          và >= 2 lần giá đóng cắt qua trục 50% EQ), loại bỏ các nhịp rơi/tăng thẳng đứng 1 chiều.
        """
        if current_price <= 0:
            return None

        sources: List[Tuple[str, Optional[Dict[str, List[float]]], Tuple[int, ...]]] = [
            ("15M", m15_klines, (16, 24, 12, 32)),
            ("1H", h1_klines, (8, 12, 6, 16)),
        ]

        best_candidate: Optional[Dict[str, Any]] = None
        best_score = -1.0

        for src_tf, kl, windows in sources:
            if not kl or len(kl.get("close", [])) < 10:
                continue
            # Dùng các nến đã ổn định trước nến kích hoạt [-2] để không nuốt mất râu SFP mới đóng
            highs_all = kl["high"][:-2]
            lows_all = kl["low"][:-2]
            closes_all = kl["close"][:-2]
            if len(closes_all) < 6:
                continue

            for win in windows:
                if len(closes_all) < win:
                    continue
                h_w = highs_all[-win:]
                l_w = lows_all[-win:]
                c_w = closes_all[-win:]

                rh = float(max(h_w))
                rl = float(min(l_w))
                span = rh - rl
                if rl <= 0 or span <= 0:
                    continue

                width_pct = (span / rl) * 100.0
                if width_pct < 0.85 or width_pct > 9.0:
                    continue

                # Giá hiện tại phải nằm bên trong hoặc sát biên Range mới hình thành
                if not (rl * 0.992 <= current_price <= rh * 1.008):
                    continue

                eq = (rh + rl) / 2.0
                upper_zone = rl + 0.65 * span
                lower_zone = rl + 0.35 * span

                upper_touches = sum(1 for h in h_w if h >= upper_zone)
                lower_touches = sum(1 for l in l_w if l <= lower_zone)
                if upper_touches < 2 or lower_touches < 2:
                    continue

                # Đếm số lần giá đóng cửa đảo qua lại quanh trục 50% EQ -> xác nhận cấu trúc đi ngang
                eq_crossings = 0
                prev_side = 0
                for c_val in c_w:
                    side = 1 if c_val >= eq else -1
                    if prev_side != 0 and side != prev_side:
                        eq_crossings += 1
                    prev_side = side
                if eq_crossings < 2:
                    continue

                # Nửa sau của cửa sổ phải còn dao động quanh Range (tránh kẹp thêm đoạn trend cũ quá dài)
                half = max(3, win // 2)
                recent_h = max(h_w[half:])
                recent_l = min(l_w[half:])
                if recent_h < rl + 0.35 * span or recent_l > rh - 0.35 * span:
                    continue

                # Điểm đánh giá độ gọn và cân bằng của Range Nội Ngày
                balance_touches = min(upper_touches, lower_touches)
                width_bonus = 3.0 if 1.0 <= width_pct <= 4.5 else 1.0
                tf_bonus = 2.0 if src_tf == "15M" else 0.5
                win_bonus = 1.5 if 16 <= win <= 24 else 0.5
                cand_score = (balance_touches * 2.5) + min(eq_crossings, 5) * 1.2 + width_bonus + tf_bonus + win_bonus

                if cand_score > best_score:
                    best_score = cand_score
                    best_candidate = {
                        "name": "INTRADAY_RANGE",
                        "label": f"Range Nội Ngày ({src_tf} · {win} nến)",
                        "range_high": round(rh, 4),
                        "range_low": round(rl, 4),
                        "eq_50": round(eq, 4),
                        "width_pct": round(width_pct, 2),
                        "weight": 4 if far_from_daily else 3,
                    }

            if best_candidate is not None:
                break

        return best_candidate

    @staticmethod
    def _detect_liquidity_pools(
        h1_klines: Optional[Dict[str, List[float]]],
        h4_klines: Optional[Dict[str, List[float]]],
        rh: float,
        rl: float,
        eq_50: float,
        tolerance_pct: float = 0.0030,
    ) -> Dict[str, Any]:
        """
        Nhận diện cụm thanh khoản Equal Highs (EQH) / Equal Lows (EQL) từ các đỉnh/đáy pivot đã đóng
        trên 1H & 4H — đây là vùng tập trung stop-loss mà TraderSZ ưu tiên săn SFP.
        """
        pivot_highs: List[float] = []
        pivot_lows: List[float] = []

        for kl in (h4_klines, h1_klines):
            if not kl or len(kl.get("close", [])) < 12:
                continue
            highs = kl["high"][:-1][-72:]
            lows = kl["low"][:-1][-72:]
            n = len(highs)
            for i in range(2, n - 2):
                if highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1] and highs[i] >= highs[i - 2] and highs[i] >= highs[i + 2] and (highs[i] > highs[i - 1] or highs[i] > highs[i + 1]):
                    pivot_highs.append(float(highs[i]))
                if lows[i] <= lows[i - 1] and lows[i] <= lows[i + 1] and lows[i] <= lows[i - 2] and lows[i] <= lows[i + 2] and (lows[i] < lows[i - 1] or lows[i] < lows[i + 1]):
                    pivot_lows.append(float(lows[i]))

        def _cluster_levels(levels: List[float]) -> List[float]:
            clusters: List[float] = []
            used = [False] * len(levels)
            for i, p1 in enumerate(levels):
                if used[i] or p1 <= 0:
                    continue
                group = [p1]
                for j in range(i + 1, len(levels)):
                    if used[j]:
                        continue
                    if abs(levels[j] - p1) / p1 <= tolerance_pct:
                        group.append(levels[j])
                        used[j] = True
                if len(group) >= 2:
                    clusters.append(round(sum(group) / len(group), 4))
            return sorted(set(clusters))

        eqh_list = _cluster_levels(pivot_highs)
        eql_list = _cluster_levels(pivot_lows)
        near_rh_eqh = any(abs(lv - rh) / max(rh, 1e-6) <= 0.0065 or (eq_50 < lv <= rh * 1.005) for lv in eqh_list)
        near_rl_eql = any(abs(lv - rl) / max(rl, 1e-6) <= 0.0065 or (rl * 0.995 <= lv < eq_50) for lv in eql_list)

        return {
            "eqh": eqh_list[-3:],
            "eql": eql_list[:3],
            "near_rh_eqh": near_rh_eqh,
            "near_rl_eql": near_rl_eql,
        }

    @staticmethod
    def _find_macro_sr_range(
        d1_klines: Optional[Dict[str, List[float]]],
        current_price: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Nhận diện Macro Structural S/R Range từ các cụm đỉnh/đáy xoay chiều (Pivot S/R) trên khung Daily (tới 365 nến):
        - Gom cụm các mốc Pivot chạm nhiều lần (multi-touch S/R trong vòng 0.85%) để chọn vùng kháng cự (RH) & hỗ trợ (RL) cứng nhất.
        - Nới biên độ hộp khung lớn tới 45% để bắt trọn các vùng S/R đa tuần / đa tháng chuẩn TraderSZ.
        """
        if not d1_klines or len(d1_klines.get("close", [])) < 30 or current_price <= 0:
            return None
        highs = d1_klines["high"][:-1][-365:]
        lows = d1_klines["low"][:-1][-365:]
        n = len(highs)
        piv_h: List[float] = []
        piv_l: List[float] = []
        for i in range(2, n - 2):
            if (
                highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1]
                and highs[i] >= highs[i - 2] and highs[i] >= highs[i + 2]
                and (highs[i] > highs[i - 1] or highs[i] > highs[i + 1])
            ):
                piv_h.append(float(highs[i]))
            if (
                lows[i] <= lows[i - 1] and lows[i] <= lows[i + 1]
                and lows[i] <= lows[i - 2] and lows[i] <= lows[i + 2]
                and (lows[i] < lows[i - 1] or lows[i] < lows[i + 1])
            ):
                piv_l.append(float(lows[i]))

        def _cluster_sr(levels: List[float], tol_pct: float = 0.0085) -> List[Tuple[float, int]]:
            clusters: List[Tuple[float, int]] = []
            used = [False] * len(levels)
            for idx_a, lv in enumerate(levels):
                if used[idx_a] or lv <= 0:
                    continue
                grp = [lv]
                used[idx_a] = True
                for idx_b in range(idx_a + 1, len(levels)):
                    if not used[idx_b] and abs(levels[idx_b] - lv) / lv <= tol_pct:
                        grp.append(levels[idx_b])
                        used[idx_b] = True
                clusters.append((sum(grp) / len(grp), len(grp)))
            return clusters

        res_clusters = _cluster_sr(piv_h)
        sup_clusters = _cluster_sr(piv_l)

        above_res = sorted(
            [c for c in res_clusters if c[0] >= current_price * 0.992],
            key=lambda c: (0 if c[1] >= 2 and c[0] <= current_price * 1.18 else 1, c[0]),
        )
        below_sup = sorted(
            [c for c in sup_clusters if c[0] <= current_price * 1.008],
            key=lambda c: (0 if c[1] >= 2 and c[0] >= current_price * 0.82 else 1, -c[0]),
        )
        if not above_res or not below_sup:
            return None

        sr_high, res_touches = above_res[0]
        sr_low, sup_touches = below_sup[0]
        if sr_high <= sr_low or sr_low <= 0:
            return None
        width_pct = (sr_high - sr_low) / sr_low * 100.0
        if width_pct < 2.5 or width_pct > 45.0:
            return None
        total_touches = res_touches + sup_touches
        lbl = f"Macro S/R Range (1D · {total_touches} chạm)" if total_touches >= 3 else "Macro S/R Range (1D)"
        return {
            "name": "MACRO_SR_RANGE",
            "label": lbl,
            "range_high": round(sr_high, 4),
            "range_low": round(sr_low, 4),
            "eq_50": round((sr_high + sr_low) / 2.0, 4),
            "width_pct": round(width_pct, 2),
            "weight": 4 if total_touches >= 3 else 3,
            "touches": total_touches,
        }

    @staticmethod
    def _detect_unswept_clean_pools(
        m15_klines: Optional[Dict[str, List[float]]],
        h1_klines: Optional[Dict[str, List[float]]],
        h4_klines: Optional[Dict[str, List[float]]],
        current_price: float,
        tolerance_pct: float = 0.0018,
    ) -> Dict[str, Any]:
        """
        Quy tắc TraderSZ về 'Clean Lows / Clean Highs':
        - Nếu dưới Entry (0.15% - 1.5%) còn cụm Đáy Bằng (Clean Lows / EQL) CHƯA BỊ QUÉT,
          giá có xu hướng bị hút xuống quét sạch đáy bằng đó trước khi đảo chiều -> CHẶN LONG sớm!
        - Tương tự nếu trên Entry (0.15% - 1.5%) còn cụm Đỉnh Bằng (Clean Highs / EQH) CHƯA BỊ QUÉT -> CHẶN SHORT sớm!
        """
        if current_price <= 0:
            return {"unswept_eql": None, "unswept_eqh": None, "all_unswept_eql": [], "all_unswept_eqh": []}

        unswept_eqls: List[float] = []
        unswept_eqhs: List[float] = []

        for kl in (m15_klines, h1_klines, h4_klines):
            if not kl or len(kl.get("close", [])) < 12:
                continue
            highs = kl["high"][:-1][-48:]
            lows = kl["low"][:-1][-48:]
            n = len(highs)

            piv_lows: List[Tuple[int, float]] = []
            piv_highs: List[Tuple[int, float]] = []
            for i in range(1, n - 2):
                if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                    piv_lows.append((i, float(lows[i])))
                if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                    piv_highs.append((i, float(highs[i])))

            # Nhóm Clean Lows chưa bị quét bởi các nến sau đó
            for idx_a in range(len(piv_lows)):
                i1, p1 = piv_lows[idx_a]
                if p1 <= 0:
                    continue
                for idx_b in range(idx_a + 1, len(piv_lows)):
                    i2, p2 = piv_lows[idx_b]
                    if abs(p2 - p1) / p1 <= tolerance_pct:
                        avg_l = (p1 + p2) / 2.0
                        # Chỉ tính là nam châm nguy hiểm nếu nằm dưới giá hiện tại 0.15%..1.50%
                        if current_price * 0.985 <= avg_l <= current_price * 0.9985:
                            # Kiểm tra xem có nến đóng nào sau đỉnh/đáy thứ 2 đã quét thủng xuống dưới cụm này chưa
                            later_lows = lows[i2 + 1:]
                            already_swept = any(ll < min(p1, p2) * 0.9992 for ll in later_lows)
                            if not already_swept:
                                unswept_eqls.append(round(avg_l, 4))

            # Nhóm Clean Highs chưa bị quét bởi các nến sau đó
            for idx_a in range(len(piv_highs)):
                i1, p1 = piv_highs[idx_a]
                if p1 <= 0:
                    continue
                for idx_b in range(idx_a + 1, len(piv_highs)):
                    i2, p2 = piv_highs[idx_b]
                    if abs(p2 - p1) / p1 <= tolerance_pct:
                        avg_h = (p1 + p2) / 2.0
                        if current_price * 1.0015 <= avg_h <= current_price * 1.015:
                            later_highs = highs[i2 + 1:]
                            already_swept = any(lh > max(p1, p2) * 1.0008 for lh in later_highs)
                            if not already_swept:
                                unswept_eqhs.append(round(avg_h, 4))

        uniq_eql = sorted(set(unswept_eqls), reverse=True)
        uniq_eqh = sorted(set(unswept_eqhs))
        return {
            "unswept_eql": uniq_eql[0] if uniq_eql else None,
            "unswept_eqh": uniq_eqh[0] if uniq_eqh else None,
            "all_unswept_eql": uniq_eql[:3],
            "all_unswept_eqh": uniq_eqh[:3],
        }

    @staticmethod
    def _check_m15_msb(
        m15_klines: Optional[Dict[str, List[float]]],
        direction: str,
        current_price: float,
    ) -> Dict[str, Any]:
        """
        Kiểm tra Xác nhận Phá vỡ Cấu trúc Khung nhỏ 15m (MSB / MSS / CHOCH) bằng nến đã đóng:
        - Với SHORT: 15m vừa đóng phá xuống dưới đáy pivot gần nhất trước đó.
        - Với LONG : 15m vừa đóng phá lên trên đỉnh pivot gần nhất trước đó.
        """
        if not m15_klines or len(m15_klines.get("close", [])) < 16 or current_price <= 0:
            return {"available": False, "confirmed": False, "broken_level": 0.0, "m15_aligned": True}

        opens = m15_klines["open"]
        highs = m15_klines["high"]
        lows = m15_klines["low"]
        closes = m15_klines["close"]

        # Nến đóng gần nhất [-2], nến trước đó [-3]
        c2_o, c2_h, c2_l, c2_c = float(opens[-2]), float(highs[-2]), float(lows[-2]), float(closes[-2])
        c3_o, c3_h, c3_l, c3_c = float(opens[-3]), float(highs[-3]), float(lows[-3]), float(closes[-3])

        c2_body_ratio = abs(c2_c - c2_o) / max(c2_h - c2_l, 1e-9)
        c3_body_ratio = abs(c3_c - c3_o) / max(c3_h - c3_l, 1e-9)

        if direction == "SHORT":
            # Tìm đáy pivot 15m gần nhất trong đoạn [-18:-3]
            pivots = []
            start = max(1, len(closes) - 18)
            end = len(closes) - 3
            for i in range(start, end):
                if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                    pivots.append(float(lows[i]))
            swing_low = pivots[-1] if pivots else float(min(lows[max(0, len(lows) - 12):-2]))
            c2_disp = (c2_c < c2_o) and (c2_body_ratio >= 0.35) and (c2_c < swing_low * 0.9995)
            c3_disp = (c3_c < c3_o) and (c3_body_ratio >= 0.35) and (c3_c < swing_low * 0.9995) and (c2_c < swing_low)
            confirmed = c2_disp or c3_disp
            aligned = confirmed or ((c2_c < c2_o) and (c2_body_ratio >= 0.40) and (c2_c < float(lows[-3])))
            return {"available": True, "confirmed": bool(confirmed), "broken_level": round(swing_low, 4), "m15_aligned": bool(aligned)}

        if direction == "LONG":
            pivots = []
            start = max(1, len(closes) - 18)
            end = len(closes) - 3
            for i in range(start, end):
                if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                    pivots.append(float(highs[i]))
            swing_high = pivots[-1] if pivots else float(max(highs[max(0, len(highs) - 12):-2]))
            c2_disp = (c2_c > c2_o) and (c2_body_ratio >= 0.35) and (c2_c > swing_high * 1.0005)
            c3_disp = (c3_c > c3_o) and (c3_body_ratio >= 0.35) and (c3_c > swing_high * 1.0005) and (c2_c > swing_high)
            confirmed = c2_disp or c3_disp
            aligned = confirmed or ((c2_c > c2_o) and (c2_body_ratio >= 0.40) and (c2_c > float(highs[-3])))
            return {"available": True, "confirmed": bool(confirmed), "broken_level": round(swing_high, 4), "m15_aligned": bool(aligned)}

        return {"available": True, "confirmed": False, "broken_level": 0.0, "m15_aligned": True}

    @staticmethod
    def calculate_htf_opens_and_ranges(
        d1_klines: Optional[Dict[str, List[float]]],
        h1_klines: Optional[Dict[str, List[float]]] = None,
        m15_klines: Optional[Dict[str, List[float]]] = None,
        current_price: float = 0.0,
        utcnow: Optional[datetime] = None,
        h4_klines: Optional[Dict[str, List[float]]] = None,
    ) -> Dict[str, Any]:
        """
        Tính chính xác theo lịch UTC:
        - Year Open (YO), Monthly Open (MO), Weekly Open (WO), Daily Open (DO)
        - Previous Day High/Low (PDH/PDL) & 50% EQ
        - Monday Range (Monday High / Monday Low & 50% EQ)
        - Previous Week High/Low (PWH/PWL & 50% EQ)
        - Previous Month High/Low (PMH/PML & 50% EQ)
        - Asian Session High/Low (00:00–08:00 UTC hôm nay)
        """
        if utcnow is None:
            utcnow = datetime.now(timezone.utc)

        if not d1_klines or len(d1_klines.get("close", [])) < 3:
            p = current_price or 100.0
            rh, rl = p * 1.02, p * 0.98
            eq = (rh + rl) / 2.0
            is_monday_forming = (utcnow.weekday() == 0 and utcnow.hour < 20)
            return {
                "daily_open": p, "weekly_open": p, "monthly_open": p, "year_open": p,
                "pdh": p * 1.015, "pdl": p * 0.985, "pd_eq": p,
                "monday_high": p * 1.015, "monday_low": p * 0.985, "monday_eq": p, "monday_ready": False,
                "monday_rule_state": "MONDAY_FORMING" if is_monday_forming else "STANDARD",
                "monday_size_mult": 0.75 if is_monday_forming else 1.0,
                "pwh": p * 1.03, "pwl": p * 0.97, "pw_eq": p,
                "pmh": rh, "pml": rl, "pm_eq": eq,
                "asia_high": 0.0, "asia_low": 0.0, "asia_eq": 0.0, "asia_ready": False,
                "range_name": "FALLBACK_RANGE",
                "range_label": "Fallback Range",
                "range_high": rh, "range_low": rl, "eq_50": eq,
                "q25": round(rl + (rh - rl) * 0.25, 4),
                "q75": round(rl + (rh - rl) * 0.75, 4),
                "range_width_pct": 4.0, "range_pos_pct": 50.0,
                "market_zone": "NO_TRADE_EQ_ZONE", "dist_to_eq_pct": 0.0,
                "liquidity_pools": {"eqh": [], "eql": [], "near_rh_eqh": False, "near_rl_eql": False},
                "all_ranges": [],
            }

        d_opens  = d1_klines.get("open", [])
        d_highs  = d1_klines.get("high", [])
        d_lows   = d1_klines.get("low", [])
        d_closes = d1_klines.get("close", [])
        d_ts     = d1_klines.get("timestamp", [])
        n = len(d_closes)

        if current_price <= 0:
            current_price = d_closes[-1]

        has_ts = bool(d_ts and len(d_ts) == n and d_ts[-1] > 0)

        # Mặc định dùng nến cuối nếu thiếu timestamp
        daily_open = d_opens[-1]
        pdh = d_highs[-2] if n >= 2 else d_highs[-1]
        pdl = d_lows[-2] if n >= 2 else d_lows[-1]

        # Xác định theo lịch UTC chuẩn nếu có timestamps
        weekly_open = d_opens[-min(7, n)]
        monthly_open = d_opens[-min(30, n)]
        year_open = d_opens[0]

        monday_high = 0.0
        monday_low = 0.0
        monday_ready = False

        pwh = max(d_highs[-min(8, n):-1]) if n >= 2 else d_highs[-1]
        pwl = min(d_lows[-min(8, n):-1]) if n >= 2 else d_lows[-1]
        pmh = max(d_highs[-min(31, n):-1]) if n >= 2 else d_highs[-1]
        pml = min(d_lows[-min(31, n):-1]) if n >= 2 else d_lows[-1]

        if has_ts:
            today_date = utcnow.date()
            cur_year = today_date.year
            cur_month = (today_date.year, today_date.month)
            cur_iso_year, cur_iso_week, _ = today_date.isocalendar()

            dates_utc = [TradersszFramework._ts_to_utc(t).date() for t in d_ts]

            # Daily Open & PDH/PDL
            today_indices = [i for i, d in enumerate(dates_utc) if d == today_date]
            if today_indices:
                t_idx = today_indices[0]
                daily_open = d_opens[t_idx]
                if t_idx > 0:
                    pdh = d_highs[t_idx - 1]
                    pdl = d_lows[t_idx - 1]
            else:
                daily_open = d_opens[-1]
                pdh = d_highs[-2] if n >= 2 else d_highs[-1]
                pdl = d_lows[-2] if n >= 2 else d_lows[-1]

            # Year Open: nến đầu tiên của năm UTC hiện tại
            year_indices = [i for i, d in enumerate(dates_utc) if d.year == cur_year]
            if year_indices:
                year_open = d_opens[year_indices[0]]

            # Monthly Open & Previous Month High/Low
            month_indices = [i for i, d in enumerate(dates_utc) if (d.year, d.month) == cur_month]
            if month_indices:
                monthly_open = d_opens[month_indices[0]]
                first_m = month_indices[0]
                prev_m_indices = [
                    i for i in range(first_m)
                    if dates_utc[i].month == (12 if cur_month[1] == 1 else cur_month[1] - 1)
                ]
                if prev_m_indices:
                    pmh = max(d_highs[i] for i in prev_m_indices)
                    pml = min(d_lows[i] for i in prev_m_indices)

            # Weekly Open, Previous Week High/Low, Monday Range
            week_indices = [
                i for i, d in enumerate(dates_utc)
                if d.isocalendar()[:2] == (cur_iso_year, cur_iso_week)
            ]
            if week_indices:
                weekly_open = d_opens[week_indices[0]]
                # Monday của tuần hiện tại (weekday == 0)
                mon_this_week = [i for i in week_indices if dates_utc[i].weekday() == 0]
                if mon_this_week:
                    m_idx = mon_this_week[0]
                    # Nếu hôm nay KHÔNG phải thứ 2 (tức thứ 2 đã đóng cửa xong) -> Monday Range chính thức
                    if today_date.weekday() != 0 or utcnow.hour >= 20:
                        monday_high = d_highs[m_idx]
                        monday_low = d_lows[m_idx]
                        monday_ready = True

                # Tuần trước
                first_w = week_indices[0]
                if first_w > 0:
                    prev_w_cal = dates_utc[first_w - 1].isocalendar()[:2]
                    prev_w_indices = [i for i in range(first_w) if dates_utc[i].isocalendar()[:2] == prev_w_cal]
                    if prev_w_indices:
                        pwh = max(d_highs[i] for i in prev_w_indices)
                        pwl = min(d_lows[i] for i in prev_w_indices)
                        # Nếu Monday tuần này chưa đóng, dùng Monday tuần trước làm tham chiếu phụ
                        if not monday_ready:
                            mon_prev = [i for i in prev_w_indices if dates_utc[i].weekday() == 0]
                            if mon_prev:
                                monday_high = d_highs[mon_prev[0]]
                                monday_low = d_lows[mon_prev[0]]
                                monday_ready = True

        # Fallback Monday Range nếu thị trường chứng khoán/tuần không có ngày thứ 2 (nghỉ lễ)
        if not monday_ready:
            monday_high = pdh
            monday_low = pdl

        # Asian Session High/Low (00:00 - 08:00 UTC hôm nay) từ 15m hoặc 1h
        asia_high = 0.0
        asia_low = 0.0
        asia_ready = False
        intraday = m15_klines if (m15_klines and m15_klines.get("timestamp")) else h1_klines
        if intraday and intraday.get("timestamp") and len(intraday["timestamp"]) == len(intraday.get("close", [])):
            today_d = utcnow.date()
            a_highs, a_lows = [], []
            for idx, ts in enumerate(intraday["timestamp"]):
                dt = TradersszFramework._ts_to_utc(ts)
                if dt.date() == today_d and 0 <= dt.hour < 8:
                    a_highs.append(intraday["high"][idx])
                    a_lows.append(intraday["low"][idx])
            if a_highs and a_lows and utcnow.hour >= 8:
                asia_high = max(a_highs)
                asia_low = min(a_lows)
                asia_ready = True

        # Xây dựng danh sách tất cả các Dealing Ranges hợp lệ
        all_ranges: List[Dict[str, Any]] = []

        def _add_range(name: str, label: str, rh: float, rl: float, weight: int):
            if rh > rl > 0:
                width_pct = (rh - rl) / rl * 100.0
                min_width = 0.90 if name == "ASIA_RANGE" else 0.85
                if width_pct >= min_width:
                    all_ranges.append({
                        "name": name,
                        "label": label,
                        "range_high": round(rh, 4),
                        "range_low": round(rl, 4),
                        "eq_50": round((rh + rl) / 2.0, 4),
                        "width_pct": round(width_pct, 2),
                        "weight": weight,
                    })

        # Ưu tiên TraderSZ: Previous Day Range (PDH/PDL), Monday Range (MonH/MonL), Previous Week Range (PWH/PWL), Asian Range
        _add_range("DAILY_RANGE", "Range Ngày Hôm Qua (PDH/PDL)", pdh, pdl, 3)
        if monday_ready and (monday_high != pdh or monday_low != pdl):
            _add_range("MONDAY_RANGE", "Monday Range (Đỉnh/Đáy Thứ Hai)", monday_high, monday_low, 4)
        _add_range("WEEKLY_RANGE", "Range Tuần Trước (PWH/PWL)", pwh, pwl, 3)
        if asia_ready:
            _add_range("ASIA_RANGE", "Range Phiên Á (00–08h UTC)", asia_high, asia_low, 2)
        if pmh > pml > 0 and (pmh != pwh or pml != pwl):
            _add_range("MONTHLY_RANGE", "Range Tháng Trước (PMH/PML)", pmh, pml, 2)

        daily_span = max(pdh - pdl, 1e-6)
        far_from_daily = bool(
            pdh > pdl > 0
            and (
                current_price > pdh * 1.006
                or current_price < pdl * 0.994
                or current_price > pdh + daily_span * 0.25
                or current_price < pdl - daily_span * 0.25
            )
        )

        # Range Nội Ngày (hình thành khi giá đi xa trong ngày rồi chuyển sang tích lũy đi ngang)
        intraday_rng = TradersszFramework._find_intraday_dealing_range(
            m15_klines=m15_klines,
            h1_klines=h1_klines,
            current_price=current_price,
            far_from_daily=far_from_daily,
        )
        if intraday_rng:
            is_dup_intra = any(
                r["name"] in ("DAILY_RANGE", "MONDAY_RANGE", "WEEKLY_RANGE")
                and abs(r["range_high"] - intraday_rng["range_high"]) / max(intraday_rng["range_high"], 1e-6) < 0.005
                and abs(r["range_low"] - intraday_rng["range_low"]) / max(intraday_rng["range_low"], 1e-6) < 0.005
                for r in all_ranges
            )
            if not is_dup_intra:
                all_ranges.append(intraday_rng)

        # Active Swing Dealing Range (4H/1D) khi giá dịch chuyển sang cấu trúc mới
        swing_rng = TradersszFramework._find_active_swing_range(h4_klines, d1_klines, current_price)
        if swing_rng:
            is_dup = any(
                abs(r["range_high"] - swing_rng["range_high"]) / max(swing_rng["range_high"], 1e-6) < 0.008
                and abs(r["range_low"] - swing_rng["range_low"]) / max(swing_rng["range_low"], 1e-6) < 0.008
                for r in all_ranges
            )
            if not is_dup:
                all_ranges.append(swing_rng)

        # Macro S/R Range (1D multi-pivot) theo phương pháp kẻ hộp S/R khung lớn trong video TraderSZ
        macro_sr_rng = TradersszFramework._find_macro_sr_range(d1_klines, current_price)
        if macro_sr_rng:
            is_dup_sr = any(
                abs(r["range_high"] - macro_sr_rng["range_high"]) / max(macro_sr_rng["range_high"], 1e-6) < 0.008
                and abs(r["range_low"] - macro_sr_rng["range_low"]) / max(macro_sr_rng["range_low"], 1e-6) < 0.008
                for r in all_ranges
            )
            if not is_dup_sr:
                all_ranges.append(macro_sr_rng)

        # Chọn Dealing Range chính làm bối cảnh tổng quan:
        # - Khi giá đã tăng/giảm xa khỏi PDH/PDL trong ngày -> ưu tiên INTRADAY_RANGE / MACRO_SR_RANGE / WEEKLY / MONTHLY.
        # - Khi DAILY_RANGE đang lơ lửng ở giữa (38% - 62%) nhưng giá lại đang test sát biên RH/RL của vùng S/R khung lớn
        #   (WEEKLY / MONTHLY / MACRO_SR_RANGE / MONDAY_RANGE) -> nâng vùng S/R khung lớn đang test lên làm Primary Range!
        # - Khi giá còn quanh biên PDH/PDL -> ưu tiên DAILY_RANGE / MONDAY_RANGE / WEEKLY_RANGE chuẩn TraderSZ.
        primary = None
        daily_rng_obj = next((r for r in all_ranges if r["name"] == "DAILY_RANGE"), None)
        daily_in_mid = False
        if daily_rng_obj and not far_from_daily:
            d_span = max(daily_rng_obj["range_high"] - daily_rng_obj["range_low"], 1e-6)
            d_pos = (current_price - daily_rng_obj["range_low"]) / d_span * 100.0
            daily_in_mid = (36.0 <= d_pos <= 64.0)

        if daily_in_mid:
            for macro_name in ("MONDAY_RANGE", "WEEKLY_RANGE", "MONTHLY_RANGE", "MACRO_SR_RANGE"):
                for r in all_ranges:
                    if r["name"] == macro_name:
                        m_span = max(r["range_high"] - r["range_low"], 1e-6)
                        m_pos = (current_price - r["range_low"]) / m_span * 100.0
                        if (
                            (r["range_low"] * 0.975 <= current_price <= r["range_high"] * 1.025)
                            and (m_pos <= 24.0 or m_pos >= 76.0)
                        ):
                            primary = r
                            break
                if primary:
                    break

        priority_order = (
            ("INTRADAY_RANGE", "ASIA_RANGE", "MONDAY_RANGE", "WEEKLY_RANGE", "MONTHLY_RANGE", "MACRO_SR_RANGE", "SWING_RANGE", "DAILY_RANGE")
            if far_from_daily
            else ("DAILY_RANGE", "MONDAY_RANGE", "WEEKLY_RANGE", "MONTHLY_RANGE", "MACRO_SR_RANGE", "INTRADAY_RANGE", "SWING_RANGE")
        )
        if primary is None:
            for cand_name in priority_order:
                for r in all_ranges:
                    if r["name"] == cand_name:
                        r_span = max(r["range_high"] - r["range_low"], 1e-6)
                        in_tight_band = (
                            (r["range_low"] - r_span * 0.35 <= current_price <= r["range_high"] + r_span * 0.35)
                            and (r["range_low"] * 0.975 <= current_price <= r["range_high"] * 1.025)
                        )
                        if in_tight_band:
                            primary = r
                            break
                if primary:
                    break
        if primary is None:
            for cand_name in priority_order:
                for r in all_ranges:
                    if r["name"] == cand_name and (r["range_low"] * 0.97 <= current_price <= r["range_high"] * 1.03):
                        primary = r
                        break
                if primary:
                    break
        if primary is None:
            primary = all_ranges[0] if all_ranges else {
                "name": "DAILY_RANGE",
                "label": "Range Ngày (PDH/PDL)",
                "range_high": round(pdh, 4),
                "range_low": round(pdl, 4),
                "eq_50": round((pdh + pdl) / 2.0, 4),
                "width_pct": round((pdh - pdl) / max(pdl, 1e-6) * 100, 2),
                "weight": 3,
            }

        rh = primary["range_high"]
        rl = primary["range_low"]
        eq_50 = primary["eq_50"]
        span = max(rh - rl, 1e-6)
        q25 = round(rl + span * 0.25, 4)
        q75 = round(rl + span * 0.75, 4)
        range_pos_pct = (current_price - rl) / span * 100.0
        dist_to_eq_pct = (current_price - eq_50) / eq_50 * 100.0 if eq_50 > 0 else 0.0

        # Phân vùng TraderSZ:
        # - Phá vỡ ngoài biên (> RH * 1.0020 / < RL * 0.9980): BREAKOUT_ABOVE_RH / BREAKOUT_BELOW_RL
        # - Vùng giữa (38% - 62% quanh 50% EQ): No-Trade Zone
        # - Vùng Premium (>= 62% trong Range): Canh SHORT khi quét RH
        # - Vùng Discount (<= 38% trong Range): Canh LONG khi quét RL
        if current_price > rh * 1.0020:
            market_zone = "BREAKOUT_ABOVE_RH"
        elif current_price < rl * 0.9980:
            market_zone = "BREAKOUT_BELOW_RL"
        elif range_pos_pct >= 62.0:
            market_zone = "PREMIUM"
        elif range_pos_pct <= 38.0:
            market_zone = "DISCOUNT"
        else:
            market_zone = "NO_TRADE_EQ_ZONE"

        # Quy tắc Monday Range & Thanh khoản EQH/EQL
        is_monday_forming = (utcnow.weekday() == 0 and utcnow.hour < 20)
        is_monday_hunt = utcnow.weekday() in (1, 2, 3)
        monday_rule_state = "MONDAY_FORMING" if is_monday_forming else ("MONDAY_HUNT_WINDOW" if is_monday_hunt else "STANDARD")
        monday_size_mult = 0.75 if is_monday_forming else 1.0

        liq_pools = TradersszFramework._detect_liquidity_pools(h1_klines, h4_klines, rh, rl, eq_50)
        unswept_clean_pools = TradersszFramework._detect_unswept_clean_pools(m15_klines, h1_klines, h4_klines, current_price)

        return {
            "daily_open": round(daily_open, 4),
            "weekly_open": round(weekly_open, 4),
            "monthly_open": round(monthly_open, 4),
            "year_open": round(year_open, 4),
            "pdh": round(pdh, 4),
            "pdl": round(pdl, 4),
            "pd_eq": round((pdh + pdl) / 2.0, 4),
            "monday_high": round(monday_high, 4),
            "monday_low": round(monday_low, 4),
            "monday_eq": round((monday_high + monday_low) / 2.0, 4),
            "monday_ready": monday_ready,
            "monday_rule_state": monday_rule_state,
            "monday_size_mult": monday_size_mult,
            "pwh": round(pwh, 4),
            "pwl": round(pwl, 4),
            "pw_eq": round((pwh + pwl) / 2.0, 4),
            "pmh": round(pmh, 4),
            "pml": round(pml, 4),
            "pm_eq": round((pmh + pml) / 2.0, 4),
            "asia_high": round(asia_high, 4),
            "asia_low": round(asia_low, 4),
            "asia_eq": round((asia_high + asia_low) / 2.0, 4) if asia_ready else 0.0,
            "asia_ready": asia_ready,
            "macro_sr_high": macro_sr_rng["range_high"] if macro_sr_rng else 0.0,
            "macro_sr_low": macro_sr_rng["range_low"] if macro_sr_rng else 0.0,
            "macro_sr_eq": macro_sr_rng["eq_50"] if macro_sr_rng else 0.0,
            "macro_sr_ready": bool(macro_sr_rng),
            "range_name": primary["name"],
            "range_label": primary["label"],
            "range_high": rh,
            "range_low": rl,
            "eq_50": eq_50,
            "q25": q25,
            "q75": q75,
            "range_width_pct": primary["width_pct"],
            "range_pos_pct": round(range_pos_pct, 1),
            "market_zone": market_zone,
            "dist_to_eq_pct": round(dist_to_eq_pct, 2),
            "far_from_daily": far_from_daily,
            "intraday_range_active": bool(intraday_rng),
            "liquidity_pools": liq_pools,
            "unswept_clean_pools": unswept_clean_pools,
            "all_ranges": all_ranges,
        }

    @staticmethod
    def _compute_min_sl_floor(
        klines: Optional[Dict[str, List[float]]],
        asset_type: str = "CRYPTO",
        h1_atr_pct: float = 0.0,
        is_breakout: bool = False,
    ) -> float:
        """Tính sàn SL tối thiểu theo ATR thực tế (ưu tiên neo tối thiểu theo ATR 1H để tránh bị râu 15m quét)."""
        bar_atr_pct = 0.0
        if klines and len(klines.get("close", [])) >= 3:
            highs = klines["high"]
            lows = klines["low"]
            closes = klines["close"]
            recent_bars = [
                (highs[j] - lows[j]) / max(closes[j], 1e-9)
                for j in range(max(0, len(closes) - 11), len(closes) - 1)
                if closes[j] > 0
            ]
            if recent_bars:
                bar_atr_pct = sum(recent_bars) / len(recent_bars)
        ref_atr = max(bar_atr_pct, float(h1_atr_pct or 0.0))
        if ref_atr <= 0:
            ref_atr = 0.0110 if str(asset_type or "CRYPTO").upper() == "CRYPTO" else 0.0065
        is_crypto = str(asset_type or "CRYPTO").upper() == "CRYPTO"
        if is_breakout:
            if is_crypto:
                return max(0.0115, min(0.0210, ref_atr * 1.00))
            return max(0.0070, min(0.0145, ref_atr * 0.90))
        if is_crypto:
            return max(0.0135, min(0.0250, ref_atr * 1.15))
        return max(0.0075, min(0.0160, ref_atr * 1.00))

    @staticmethod
    def _scan_tf_deviation(
        tf_name: str,
        klines: Optional[Dict[str, List[float]]],
        range_obj: Dict[str, Any],
        current_price: float,
        sl_buffer_pct: float = 0.0065,
        min_rr: float = 2.0,
        asset_type: str = "CRYPTO",
        h1_atr_pct: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Kiểm tra Deviation / SFP (Swing Failure Pattern) & Quy luật 'Return to Range'
        trên một khung thời gian (1d, 4h, 1h, 15m) đối với một Range cụ thể (RH, RL, EQ).

        Hỗ trợ cả 2 biến thể chuẩn TraderSZ:
        1) Single-candle SFP: Cây nến mới đóng (hoặc nến hiện tại nếu đã xác nhận râu)
           đẩy râu vượt RH rồi đóng ngược xuống dưới RH (hoặc thủng RL rồi đóng ngược lên trên RL).
        2) Multi-candle Deviation & Return to Range: 1–4 cây nến trước đóng cửa bên ngoài
           RH/RL (bẫy đám đông FOMO breakout), sau đó nến xác nhận ĐÓNG CỬA quay lại BÊN TRONG Range.
        """
        if not klines or len(klines.get("close", [])) < 8:
            return None

        opens  = klines["open"]
        highs  = klines["high"]
        lows   = klines["low"]
        closes = klines["close"]
        vols   = klines.get("volume", [1.0] * len(closes))

        rh = float(range_obj["range_high"])
        rl = float(range_obj["range_low"])
        eq = float(range_obj["eq_50"])
        r_name = range_obj["name"]
        r_label = range_obj["label"]
        r_weight = int(range_obj.get("weight", 2))

        if rh <= rl or current_price <= 0:
            return None

        pos_in_range_pct = (current_price - rl) / (rh - rl) * 100.0

        # Trọng số theo khung thời gian (TraderSZ ưu tiên H1, H4, D1; M15 dùng để bóp SL sắc nét)
        tf_weight = {"1d": 4, "4h": 4, "1h": 3, "15m": 2}.get(tf_name, 1)

        # CHỈ dùng các nến ĐÃ ĐÓNG CỬA (idx = -2 là nến vừa đóng gần nhất, idx = -3 là nến trước đó nếu -2 giữ vững trong Range)
        # Tuyệt đối KHÔNG dùng nến [-1] đang chạy dở để tránh tín hiệu nhảy/đảo chiều giữa nến.
        avg_vol = sum(vols[-21:-1]) / max(1, len(vols[-21:-1])) if len(vols) >= 6 else 1.0
        # Tính sàn SL tối thiểu dựa trên max(ATR khung hiện tại, ATR 1H) để không đặt SL mỏng hơn râu quét thanh khoản
        min_sl_floor = TradersszFramework._compute_min_sl_floor(
            klines=klines,
            asset_type=asset_type,
            h1_atr_pct=h1_atr_pct,
            is_breakout=False,
        )

        candidates: List[Dict[str, Any]] = []

        # ── A. KIỂM TRA BEARISH DEVIATION / SFP TẠI RANGE HIGH -> SHORT ──
        # Điều kiện chuẩn TraderSZ: Giá nằm BÊN DƯỚI RH tối thiểu 0.05% và ở sát vùng biên trên
        # (>= 74% Range; riêng 15m >= 78%) để không Short sớm ở vùng lửng 62–73% dưới PDH.
        min_premium_pct = 78.0 if tf_name == "15m" else 74.0
        if current_price < rh * 0.9995 and pos_in_range_pct >= min_premium_pct:
            for idx in (-2, -3):
                abs_idx = len(closes) + idx
                if abs_idx < 1:
                    continue
                if idx == -3:
                    # Nến vừa đóng [-2] phải giữ được bên dưới RH và không phá đỉnh cao mới
                    if closes[-2] >= rh or highs[-2] > rh * 1.0030:
                        continue

                c_o, c_h, c_l, c_c = opens[idx], highs[idx], lows[idx], closes[idx]
                c_span = max(c_h - c_l, 1e-9)
                upper_wick = c_h - max(c_o, c_c)
                wick_ratio = upper_wick / c_span

                # Single-candle SFP trên nến đã đóng: High vượt RH rõ ràng >= 0.12%, Close quay lại dưới RH >= 0.08%
                is_single_sfp = (
                    (c_h > rh * 1.0012)
                    and (c_c < rh * 0.9992)
                    and (
                        wick_ratio >= 0.35
                        or (c_c < c_o and ((c_o - c_c) / c_span) >= 0.35 and c_c < rh * 0.9990)
                    )
                )

                # Multi-candle Deviation: 1-4 nến đóng trước idx từng đóng trên RH, và nến idx đóng ngược vào trong (< RH)
                prev_window = closes[max(0, abs_idx - 4): abs_idx]
                was_outside = any(pc > rh * 1.0008 for pc in prev_window)
                is_return_to_range = was_outside and (c_c < rh * 0.9992)

                if is_single_sfp or is_return_to_range:
                    # Đỉnh râu Deviation cao nhất trong cụm quét kết thúc tại nến đóng idx
                    sweep_extreme = max(highs[max(0, abs_idx - 4): abs_idx + 1])
                    sl_price = round(sweep_extreme * (1.0 + sl_buffer_pct), 4)
                    is_retest = abs(current_price - rh) / rh <= 0.0050

                    # Nếu nến SFP rút râu quá sâu vào trong Range (>0.55% cách RH & >18% chiều cao Range):
                    # Đề xuất LIMIT_RETEST sát biên RH thay vì đuổi Market làm SL bị xa.
                    retrace_dist_pct = abs(current_price - rh) / rh
                    depth_in_range = (rh - current_price) / max(rh - rl, 1e-9)
                    use_limit_retest = bool((not is_retest) and retrace_dist_pct > 0.0055 and depth_in_range > 0.18 and current_price > eq * 1.002)
                    eval_entry = round(rh * 0.9985, 4) if use_limit_retest else round(current_price, 4)
                    entry_mode = "LIMIT_RETEST" if use_limit_retest else "MARKET"

                    if (sl_price - eval_entry) / eval_entry < min_sl_floor:
                        sl_price = round(eval_entry * (1.0 + min_sl_floor), 4)
                    if eval_entry >= sl_price:
                        continue

                    risk = sl_price - eval_entry
                    tp1 = round(eq, 4)
                    tp2 = round(rl, 4)
                    reward_tp1 = eval_entry - tp1
                    reward_tp2 = eval_entry - tp2

                    if risk <= 0 or reward_tp1 <= 0 or reward_tp2 <= 0:
                        continue
                    if reward_tp1 / eval_entry < 0.0035:
                        continue

                    sl_pct = risk / eval_entry
                    if sl_pct > 0.052:  # Râu quá rộng (>5.2%), bỏ qua để kiếm kèo hợp lý hơn
                        continue

                    rr_tp1 = reward_tp1 / risk
                    rr_tp2 = reward_tp2 / risk
                    if rr_tp2 < min_rr or rr_tp1 < 0.85:
                        continue

                    setup_type = "DEVIATION_RETURN_TO_RANGE" if is_return_to_range else "SFP_WICK_REJECTION"
                    vol_ratio = vols[idx] / avg_vol if avg_vol > 0 else 1.0
                    is_failed_breakout = bool(is_return_to_range and r_name in ("DAILY_RANGE", "MONDAY_RANGE", "WEEKLY_RANGE", "MONTHLY_RANGE", "MACRO_SR_RANGE", "SWING_RANGE"))

                    candidates.append({
                        "detected": True,
                        "direction": "SHORT",
                        "tf": tf_name,
                        "range_name": r_name,
                        "range_label": r_label,
                        "setup_type": setup_type,
                        "entry_mode": entry_mode,
                        "limit_entry": eval_entry,
                        "is_retest": is_retest,
                        "is_failed_breakout": is_failed_breakout,
                        "swept_level": round(rh, 4),
                        "sweep_extreme": round(sweep_extreme, 4),
                        "entry": round(current_price, 4),
                        "sl": sl_price,
                        "sl_pct": round(sl_pct * 100, 2),
                        "tp1": tp1,
                        "tp2": tp2,
                        "rr_tp1": round(rr_tp1, 2),
                        "rr_tp2": round(rr_tp2, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "score": tf_weight * 10 + r_weight * 5 + (6 if is_retest else 0) + (4 if rr_tp2 >= 2.5 else 0) + (5 if is_failed_breakout else 0),
                        "reason": (
                            f"TraderSZ {tf_name.upper()} Bearish {setup_type}: "
                            f"Quét thanh khoản trên {r_label} (${rh:.4f}, đỉnh râu ${sweep_extreme:.4f}) "
                            f"và đóng nến quay trở lại vào trong Range -> SHORT về EQ 50% (${tp1:.4f}) & Range Low (${tp2:.4f})."
                        ),
                    })
                    break

        # ── B. KIỂM TRA BULLISH DEVIATION / SFP TẠI RANGE LOW -> LONG ──
        # Giá hiện tại phải nằm BÊN TRÊN RL tối thiểu 0.05% và ở sát biên dưới (<= 26%; riêng 15m <= 22%).
        max_discount_pct = 22.0 if tf_name == "15m" else 26.0
        if current_price > rl * 1.0005 and pos_in_range_pct <= max_discount_pct:
            for idx in (-2, -3):
                abs_idx = len(closes) + idx
                if abs_idx < 1:
                    continue
                if idx == -3:
                    # Nến vừa đóng [-2] phải giữ được bên trên RL và không thủng đáy mới
                    if closes[-2] <= rl or lows[-2] < rl * 0.9970:
                        continue

                c_o, c_h, c_l, c_c = opens[idx], highs[idx], lows[idx], closes[idx]
                c_span = max(c_h - c_l, 1e-9)
                lower_wick = min(c_o, c_c) - c_l
                wick_ratio = lower_wick / c_span

                is_single_sfp = (
                    (c_l < rl * 0.9988)
                    and (c_c > rl * 1.0008)
                    and (
                        wick_ratio >= 0.35
                        or (c_c > c_o and ((c_c - c_o) / c_span) >= 0.35 and c_c > rl * 1.0010)
                    )
                )

                prev_window = closes[max(0, abs_idx - 4): abs_idx]
                was_outside = any(pc < rl * 0.9992 for pc in prev_window)
                is_return_to_range = was_outside and (c_c > rl * 1.0008)

                if is_single_sfp or is_return_to_range:
                    sweep_extreme = min(lows[max(0, abs_idx - 4): abs_idx + 1])
                    sl_price = round(sweep_extreme * (1.0 - sl_buffer_pct), 4)
                    is_retest = abs(current_price - rl) / rl <= 0.0050

                    retrace_dist_pct = abs(current_price - rl) / rl
                    depth_in_range = (current_price - rl) / max(rh - rl, 1e-9)
                    use_limit_retest = bool((not is_retest) and retrace_dist_pct > 0.0055 and depth_in_range > 0.18 and current_price < eq * 0.998)
                    eval_entry = round(rl * 1.0015, 4) if use_limit_retest else round(current_price, 4)
                    entry_mode = "LIMIT_RETEST" if use_limit_retest else "MARKET"

                    if (eval_entry - sl_price) / eval_entry < min_sl_floor:
                        sl_price = round(eval_entry * (1.0 - min_sl_floor), 4)
                    if eval_entry <= sl_price:
                        continue

                    risk = eval_entry - sl_price
                    tp1 = round(eq, 4)
                    tp2 = round(rh, 4)
                    reward_tp1 = tp1 - eval_entry
                    reward_tp2 = tp2 - eval_entry

                    if risk <= 0 or reward_tp1 <= 0 or reward_tp2 <= 0:
                        continue
                    if reward_tp1 / eval_entry < 0.0035:
                        continue

                    sl_pct = risk / eval_entry
                    if sl_pct > 0.052:
                        continue

                    rr_tp1 = reward_tp1 / risk
                    rr_tp2 = reward_tp2 / risk
                    if rr_tp2 < min_rr or rr_tp1 < 0.85:
                        continue

                    setup_type = "DEVIATION_RETURN_TO_RANGE" if is_return_to_range else "SFP_WICK_REJECTION"
                    vol_ratio = vols[idx] / avg_vol if avg_vol > 0 else 1.0
                    is_failed_breakout = bool(is_return_to_range and r_name in ("DAILY_RANGE", "MONDAY_RANGE", "WEEKLY_RANGE", "MONTHLY_RANGE", "MACRO_SR_RANGE", "SWING_RANGE"))

                    candidates.append({
                        "detected": True,
                        "direction": "LONG",
                        "tf": tf_name,
                        "range_name": r_name,
                        "range_label": r_label,
                        "setup_type": setup_type,
                        "entry_mode": entry_mode,
                        "limit_entry": eval_entry,
                        "is_retest": is_retest,
                        "is_failed_breakout": is_failed_breakout,
                        "swept_level": round(rl, 4),
                        "sweep_extreme": round(sweep_extreme, 4),
                        "entry": round(current_price, 4),
                        "sl": sl_price,
                        "sl_pct": round(sl_pct * 100, 2),
                        "tp1": tp1,
                        "tp2": tp2,
                        "rr_tp1": round(rr_tp1, 2),
                        "rr_tp2": round(rr_tp2, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "score": tf_weight * 10 + r_weight * 5 + (6 if is_retest else 0) + (4 if rr_tp2 >= 2.5 else 0) + (5 if is_failed_breakout else 0),
                        "reason": (
                            f"TraderSZ {tf_name.upper()} Bullish {setup_type}: "
                            f"Quét thanh khoản dưới {r_label} (${rl:.4f}, đáy râu ${sweep_extreme:.4f}) "
                            f"và đóng nến quay trở lại vào trong Range -> LONG về EQ 50% (${tp1:.4f}) & Range High (${tp2:.4f})."
                        ),
                    })
                    break

        if not candidates:
            return None
        return max(candidates, key=lambda x: x["score"])

    @staticmethod
    def _scan_level_reclaim(
        h1_klines: Optional[Dict[str, List[float]]],
        htf_data: Dict[str, Any],
        allowed_dir: str,
        current_price: float,
        sl_buffer_pct: float = 0.0065,
        min_rr: float = 2.0,
        asset_type: str = "CRYPTO",
        h1_atr_pct: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Bắt mô hình RECLAIM & S/R FLIP chuẩn TraderSZ tại Daily Open (DO) / Weekly Open (WO)
        nằm trong vùng hợp lệ của Range (LONG trong DISCOUNT, SHORT trong PREMIUM).
        """
        if allowed_dir not in ("LONG", "SHORT") or not h1_klines or len(h1_klines.get("close", [])) < 10:
            return None

        rh = float(htf_data.get("range_high", 0) or 0)
        rl = float(htf_data.get("range_low", 0) or 0)
        eq = float(htf_data.get("eq_50", 0) or 0)
        if rh <= rl or current_price <= 0:
            return None

        opens = h1_klines["open"]
        highs = h1_klines["high"]
        lows = h1_klines["low"]
        closes = h1_klines["close"]

        levels = [
            ("WEEKLY_OPEN", "Weekly Open (WO)", float(htf_data.get("weekly_open", 0) or 0)),
            ("MONTHLY_OPEN", "Monthly Open (MO)", float(htf_data.get("monthly_open", 0) or 0)),
            ("DAILY_OPEN", "Daily Open (DO)", float(htf_data.get("daily_open", 0) or 0)),
            ("PDH", "PDH Flip", float(htf_data.get("pdh", 0) or 0)),
            ("PDL", "PDL Flip", float(htf_data.get("pdl", 0) or 0)),
            ("MONDAY_HIGH", "Monday High Flip", float(htf_data.get("monday_high", 0) or 0)),
            ("MONDAY_LOW", "Monday Low Flip", float(htf_data.get("monday_low", 0) or 0)),
            ("PWH", "Weekly High (PWH) Flip", float(htf_data.get("pwh", 0) or 0)),
            ("PWL", "Weekly Low (PWL) Flip", float(htf_data.get("pwl", 0) or 0)),
            ("PMH", "Monthly High (PMH) Flip", float(htf_data.get("pmh", 0) or 0)),
            ("PML", "Monthly Low (PML) Flip", float(htf_data.get("pml", 0) or 0)),
            ("MACRO_SR_H", "1D Macro Resistance Flip", float(htf_data.get("macro_sr_high", 0) or 0)),
            ("MACRO_SR_L", "1D Macro Support Flip", float(htf_data.get("macro_sr_low", 0) or 0)),
        ]
        c2_c = float(closes[-2])
        c2_o = float(opens[-2])
        c2_body_ratio = abs(c2_c - c2_o) / max(float(highs[-2]) - float(lows[-2]), 1e-9)
        if c2_body_ratio < 0.35:
            return None
        span_r = max(rh - rl, 1e-9)
        reclaim_min_sl = TradersszFramework._compute_min_sl_floor(
            klines=h1_klines,
            asset_type=asset_type,
            h1_atr_pct=h1_atr_pct,
            is_breakout=False,
        )

        best_reclaim = None
        for lvl_key, lvl_label, lvl_p in levels:
            if lvl_p <= 0 or not (rl * 1.001 <= lvl_p <= rh * 0.999):
                continue

            if allowed_dir == "LONG" and rl < lvl_p <= rl + 0.35 * span_r:
                # Quét dưới level trong 4 nến đóng trước [-6:-2], nến [-2] đóng tái chiếm lên trên level
                was_below = any(closes[i] < lvl_p * 0.9992 or lows[i] < lvl_p * 0.9980 for i in range(-6, -2))
                reclaimed = (c2_c > lvl_p * 1.0008) and (c2_c >= c2_o) and (current_price > lvl_p * 1.0005)
                if was_below and reclaimed:
                    sweep_low = float(min(lows[-6:-1]))
                    sl_p = round(min(sweep_low, lvl_p * 0.994) * (1.0 - sl_buffer_pct), 4)
                    if (current_price - sl_p) / current_price < reclaim_min_sl:
                        sl_p = round(current_price * (1.0 - reclaim_min_sl), 4)
                    risk = current_price - sl_p
                    tp1, tp2 = round(eq, 4), round(rh, 4)
                    if risk > 0 and (risk / current_price) <= 0.048 and tp1 > current_price and tp2 > tp1:
                        rr2 = (tp2 - current_price) / risk
                        rr1 = (tp1 - current_price) / risk
                        if rr2 >= min_rr and rr1 >= 0.85:
                            cand = {
                                "detected": True,
                                "direction": "LONG",
                                "tf": "1h",
                                "range_name": htf_data.get("range_name", "DAILY_RANGE"),
                                "range_label": f"Reclaim {lvl_label}",
                                "setup_type": "LEVEL_RECLAIM_SR_FLIP",
                                "is_retest": abs(current_price - lvl_p) / lvl_p <= 0.0040,
                                "is_failed_breakout": False,
                                "swept_level": round(lvl_p, 4),
                                "sweep_extreme": round(sweep_low, 4),
                                "entry": round(current_price, 4),
                                "sl": sl_p,
                                "sl_pct": round((risk / current_price) * 100, 2),
                                "tp1": tp1,
                                "tp2": tp2,
                                "rr_tp1": round(rr1, 2),
                                "rr_tp2": round(rr2, 2),
                                "vol_ratio": 1.0,
                                "score": 38 + (4 if lvl_key == "WEEKLY_OPEN" else 0) + (4 if rr2 >= 2.5 else 0),
                                "reason": (
                                    f"TraderSZ 1H Bullish Reclaim & S/R Flip tại {lvl_label} (${lvl_p:.4f}): "
                                    f"Quét thanh khoản xuống ${sweep_low:.4f} rồi đóng nến tái chiếm trên mốc Open -> LONG về EQ (${tp1:.4f}) & RH (${tp2:.4f})."
                                ),
                            }
                            if not best_reclaim or cand["score"] > best_reclaim["score"]:
                                best_reclaim = cand

            elif allowed_dir == "SHORT" and rl + 0.65 * span_r <= lvl_p < rh:
                was_above = any(closes[i] > lvl_p * 1.0008 or highs[i] > lvl_p * 1.0020 for i in range(-6, -2))
                lost_level = (c2_c < lvl_p * 0.9992) and (c2_c <= c2_o) and (current_price < lvl_p * 0.9995)
                if was_above and lost_level:
                    sweep_high = float(max(highs[-6:-1]))
                    sl_p = round(max(sweep_high, lvl_p * 1.006) * (1.0 + sl_buffer_pct), 4)
                    if (sl_p - current_price) / current_price < reclaim_min_sl:
                        sl_p = round(current_price * (1.0 + reclaim_min_sl), 4)
                    risk = sl_p - current_price
                    tp1, tp2 = round(eq, 4), round(rl, 4)
                    if risk > 0 and (risk / current_price) <= 0.048 and tp1 < current_price and tp2 < tp1:
                        rr2 = (current_price - tp2) / risk
                        rr1 = (current_price - tp1) / risk
                        if rr2 >= min_rr and rr1 >= 0.85:
                            cand = {
                                "detected": True,
                                "direction": "SHORT",
                                "tf": "1h",
                                "range_name": htf_data.get("range_name", "DAILY_RANGE"),
                                "range_label": f"Reclaim Failure {lvl_label}",
                                "setup_type": "LEVEL_RECLAIM_SR_FLIP",
                                "is_retest": abs(current_price - lvl_p) / lvl_p <= 0.0040,
                                "is_failed_breakout": False,
                                "swept_level": round(lvl_p, 4),
                                "sweep_extreme": round(sweep_high, 4),
                                "entry": round(current_price, 4),
                                "sl": sl_p,
                                "sl_pct": round((risk / current_price) * 100, 2),
                                "tp1": tp1,
                                "tp2": tp2,
                                "rr_tp1": round(rr1, 2),
                                "rr_tp2": round(rr2, 2),
                                "vol_ratio": 1.0,
                                "score": 38 + (4 if lvl_key == "WEEKLY_OPEN" else 0) + (4 if rr2 >= 2.5 else 0),
                                "reason": (
                                    f"TraderSZ 1H Bearish S/R Flip tại {lvl_label} (${lvl_p:.4f}): "
                                    f"Đẩy râu lên ${sweep_high:.4f} rồi đóng nến đánh mất mốc Open -> SHORT về EQ (${tp1:.4f}) & RL (${tp2:.4f})."
                                ),
                            }
                            if not best_reclaim or cand["score"] > best_reclaim["score"]:
                                best_reclaim = cand

        return best_reclaim

    @staticmethod
    def _scan_tf_breakout(
        tf_name: str,
        klines: Optional[Dict[str, List[float]]],
        range_obj: Dict[str, Any],
        current_price: float,
        sl_buffer_pct: float = 0.0055,
        min_rr: float = 1.5,
        asset_type: str = "CRYPTO",
        h1_atr_pct: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Bắt tín hiệu PHÁ VỠ RANGE TIẾP DIỄN XU HƯỚNG (Range Breakout Continuation):
        - Phá vỡ thật (Acceptance / Nến đóng dứt khoát ngoài biên + Break & Retest), KHÔNG bắt râu giả.
        - Chống FOMO đuổi đỉnh/đáy: Chỉ kích hoạt khi giá còn trong vùng Breakout tươi/Retest (<= 1.8% & <= 35% chiều cao Range ngoài biên).
        """
        if not klines or len(klines.get("close", [])) < 10:
            return None

        opens  = klines["open"]
        highs  = klines["high"]
        lows   = klines["low"]
        closes = klines["close"]
        vols   = klines.get("volume", [1.0] * len(closes))

        rh = float(range_obj["range_high"])
        rl = float(range_obj["range_low"])
        r_name = range_obj["name"]
        r_label = range_obj["label"]
        r_weight = int(range_obj.get("weight", 2))
        range_height = rh - rl

        if rh <= rl or range_height <= 0 or current_price <= 0:
            return None
        if r_name not in ("DAILY_RANGE", "MONDAY_RANGE", "WEEKLY_RANGE", "MONTHLY_RANGE", "SWING_RANGE", "MACRO_SR_RANGE"):
            return None

        tf_weight = {"1d": 4, "4h": 4, "1h": 3, "15m": 2}.get(tf_name, 1)
        avg_vol = sum(vols[-20:-1]) / max(1, len(vols[-20:-1])) if len(vols) >= 6 else 1.0
        bo_min_sl = TradersszFramework._compute_min_sl_floor(
            klines=klines,
            asset_type=asset_type,
            h1_atr_pct=h1_atr_pct,
            is_breakout=True,
        )
        candidates: List[Dict[str, Any]] = []

        # ── A. BULLISH BREAKOUT CONTINUATION (PHÁ ĐỈNH RH >= 0.20% -> BUY TIẾP DIỄN) ──
        if current_price > rh * 1.0020:
            ext_pct = (current_price - rh) / rh
            ext_span = (current_price - rh) / range_height
            c2_o, c2_h, c2_l, c2_c = opens[-2], highs[-2], lows[-2], closes[-2]
            c3_c = closes[-3]

            # 1) Acceptance trên nến ĐÃ ĐÓNG: 2 cây nến đóng liên tiếp nằm trên RH
            is_acceptance = (c3_c > rh * 1.0010 and c2_c > rh * 1.0015)

            # 2) Strong expansion close trên nến vừa đóng [-2]: thân >= 45%, đóng vượt RH >= 0.30%
            c2_span = max(c2_h - c2_l, 1e-9)
            c2_body = c2_c - c2_o
            is_strong_break = (
                c2_body > 0
                and (c2_body / c2_span) >= 0.45
                and c2_c > rh * 1.0030
                and any(pl <= rh * 1.0025 for pl in lows[-6:-1])
            )

            # 3) Closed Break & Retest: nến trước từng đóng > RH, nến [-2] retest sát RH rồi đóng lại > RH
            had_break = any(pc > rh * 1.0015 for pc in closes[-6:-2])
            is_retest = had_break and (c2_l <= rh * 1.0050 and c2_c > rh * 1.0010)

            # Chống đuổi FOMO quá xa ngoài biên Range (>1.8% hoặc >35% chiều cao Range)
            not_overextended = (ext_pct <= 0.018 and ext_span <= 0.35) or (is_retest and ext_pct <= 0.008)

            if not_overextended and (is_acceptance or is_strong_break or is_retest):
                recent_low = min(lows[-3:-1])
                support_anchor = min(rh, recent_low)
                if (current_price - support_anchor) / current_price > 0.025:
                    support_anchor = recent_low if recent_low < current_price else rh
                sl_price = round(support_anchor * (1.0 - sl_buffer_pct), 4)
                if (current_price - sl_price) / current_price < bo_min_sl:
                    sl_price = round(current_price * (1.0 - bo_min_sl), 4)

                risk = current_price - sl_price
                sl_pct = risk / current_price
                if risk > 0 and 0.0065 <= sl_pct <= 0.038:
                    tp1 = round(max(current_price + risk * 1.25, rh + range_height * 0.50), 4)
                    tp2 = round(max(current_price + risk * max(min_rr, 2.2), rh + range_height * 1.00), 4)
                    rr_tp1 = (tp1 - current_price) / risk
                    rr_tp2 = (tp2 - current_price) / risk
                    if rr_tp2 >= min_rr and tp2 > tp1 > current_price:
                        vol_ratio = vols[-2] / avg_vol if avg_vol > 0 else 1.0
                        sub_mode = "Break & Retest" if is_retest else ("2-Candle Acceptance" if is_acceptance else "Expansion Break")
                        candidates.append({
                            "detected": True,
                            "direction": "LONG",
                            "tf": tf_name,
                            "range_name": r_name,
                            "range_label": r_label,
                            "setup_type": "RANGE_BREAKOUT_CONTINUATION",
                            "sub_mode": sub_mode,
                            "is_retest": bool(is_retest),
                            "swept_level": round(rh, 4),
                            "sweep_extreme": round(support_anchor, 4),
                            "entry": round(current_price, 4),
                            "sl": sl_price,
                            "sl_pct": round(sl_pct * 100, 2),
                            "tp1": tp1,
                            "tp2": tp2,
                            "rr_tp1": round(rr_tp1, 2),
                            "rr_tp2": round(rr_tp2, 2),
                            "vol_ratio": round(vol_ratio, 2),
                            "score": tf_weight * 10 + r_weight * 5 + (6 if is_retest else 2) + (4 if rr_tp2 >= 2.5 else 0),
                            "reason": (
                                f"TraderSZ {tf_name.upper()} Bullish Breakout ({sub_mode}): "
                                f"Phá vỡ dứt khoát đỉnh {r_label} (${rh:.4f}) & giữ vững trên biên -> "
                                f"BUY tiếp diễn xu hướng tăng (SL ${sl_price:.4f}, TP1 ${tp1:.4f}, TP2 ${tp2:.4f})."
                            ),
                        })

        # ── B. BEARISH BREAKOUT CONTINUATION (PHÁ ĐÁY RL >= 0.20% -> SELL TIẾP DIỄN) ──
        if current_price < rl * 0.9980:
            ext_pct = (rl - current_price) / rl
            ext_span = (rl - current_price) / range_height
            c2_o, c2_h, c2_l, c2_c = opens[-2], highs[-2], lows[-2], closes[-2]
            c3_c = closes[-3]

            is_acceptance = (c3_c < rl * 0.9990 and c2_c < rl * 0.9985)

            c2_span = max(c2_h - c2_l, 1e-9)
            c2_body = c2_o - c2_c
            is_strong_break = (
                c2_body > 0
                and (c2_body / c2_span) >= 0.45
                and c2_c < rl * 0.9970
                and any(ph >= rl * 0.9975 for ph in highs[-6:-1])
            )

            had_break = any(pc < rl * 0.9985 for pc in closes[-6:-2])
            is_retest = had_break and (c2_h >= rl * 0.9950 and c2_c < rl * 0.9990)

            not_overextended = (ext_pct <= 0.018 and ext_span <= 0.35) or (is_retest and ext_pct <= 0.008)

            if not_overextended and (is_acceptance or is_strong_break or is_retest):
                recent_high = max(highs[-3:-1])
                resist_anchor = max(rl, recent_high)
                if (resist_anchor - current_price) / current_price > 0.025:
                    resist_anchor = recent_high if recent_high > current_price else rl
                sl_price = round(resist_anchor * (1.0 + sl_buffer_pct), 4)
                if (sl_price - current_price) / current_price < bo_min_sl:
                    sl_price = round(current_price * (1.0 + bo_min_sl), 4)

                risk = sl_price - current_price
                sl_pct = risk / current_price
                if risk > 0 and 0.0065 <= sl_pct <= 0.038:
                    tp1 = round(min(current_price - risk * 1.25, rl - range_height * 0.50), 4)
                    tp2 = round(min(current_price - risk * max(min_rr, 2.2), rl - range_height * 1.00), 4)
                    if tp2 > 0 and tp2 < tp1 < current_price:
                        rr_tp1 = (current_price - tp1) / risk
                        rr_tp2 = (current_price - tp2) / risk
                        if rr_tp2 >= min_rr:
                            vol_ratio = vols[-2] / avg_vol if avg_vol > 0 else 1.0
                            sub_mode = "Break & Retest" if is_retest else ("2-Candle Acceptance" if is_acceptance else "Expansion Break")
                            candidates.append({
                                "detected": True,
                                "direction": "SHORT",
                                "tf": tf_name,
                                "range_name": r_name,
                                "range_label": r_label,
                                "setup_type": "RANGE_BREAKOUT_CONTINUATION",
                                "sub_mode": sub_mode,
                                "is_retest": bool(is_retest),
                                "swept_level": round(rl, 4),
                                "sweep_extreme": round(resist_anchor, 4),
                                "entry": round(current_price, 4),
                                "sl": sl_price,
                                "sl_pct": round(sl_pct * 100, 2),
                                "tp1": tp1,
                                "tp2": tp2,
                                "rr_tp1": round(rr_tp1, 2),
                                "rr_tp2": round(rr_tp2, 2),
                                "vol_ratio": round(vol_ratio, 2),
                                "score": tf_weight * 10 + r_weight * 5 + (6 if is_retest else 2) + (4 if rr_tp2 >= 2.5 else 0),
                                "reason": (
                                    f"TraderSZ {tf_name.upper()} Bearish Breakout ({sub_mode}): "
                                    f"Phá vỡ dứt khoát đáy {r_label} (${rl:.4f}) & giữ dưới biên -> "
                                    f"SELL tiếp diễn xu hướng giảm (SL ${sl_price:.4f}, TP1 ${tp1:.4f}, TP2 ${tp2:.4f})."
                                ),
                            })

        if not candidates:
            return None
        return max(candidates, key=lambda x: x["score"])

    @staticmethod
    def evaluate_htf_bias(
        htf_data: Dict[str, Any],
        d1_klines: Optional[Dict[str, List[float]]],
        h4_klines: Optional[Dict[str, List[float]]],
        current_price: float,
    ) -> Dict[str, Any]:
        """
        Thiên hướng khung lớn (HTF Bias) thuần Price Action của TraderSZ:
        - So sánh vị thế giá với Year Open, Monthly Open, Weekly Open, Daily Open.
        - Đánh giá cấu trúc đỉnh/đáy thuần túy trên 4H & D1 (không dùng chỉ báo).
        """
        yo = htf_data.get("year_open", current_price)
        mo = htf_data.get("monthly_open", current_price)
        wo = htf_data.get("weekly_open", current_price)
        do = htf_data.get("daily_open", current_price)

        above_yo = current_price >= yo
        above_mo = current_price >= mo
        above_wo = current_price >= wo
        above_do = current_price >= do

        bull_opens = sum([above_yo, above_mo, above_wo, above_do])

        # Kiểm tra cấu trúc Swing Pivot thực tế (HH/HL vs LH/LL) trên 4H (tối đa 48 nến đã đóng)
        h4_bias = "NEUTRAL"
        if h4_klines and len(h4_klines.get("close", [])) >= 12:
            h4_h = h4_klines["high"][:-1][-48:]
            h4_l = h4_klines["low"][:-1][-48:]
            piv_highs = []
            piv_lows = []
            for i in range(1, len(h4_h) - 1):
                if h4_h[i] > h4_h[i - 1] and h4_h[i] >= h4_h[i + 1]:
                    piv_highs.append(float(h4_h[i]))
                if h4_l[i] < h4_l[i - 1] and h4_l[i] <= h4_l[i + 1]:
                    piv_lows.append(float(h4_l[i]))

            if len(piv_highs) >= 2 and len(piv_lows) >= 2:
                prev_h, curr_h = piv_highs[-2], piv_highs[-1]
                prev_l, curr_l = piv_lows[-2], piv_lows[-1]
                if curr_h > prev_h and curr_l > prev_l:
                    h4_bias = "BULLISH"
                elif curr_h < prev_h and curr_l < prev_l:
                    h4_bias = "BEARISH"
                elif current_price > curr_h * 1.002 and curr_l >= prev_l:
                    h4_bias = "BULLISH"
                elif current_price < curr_l * 0.998 and curr_h <= prev_h:
                    h4_bias = "BEARISH"
                elif curr_h > prev_h and curr_l < prev_l:
                    h4_bias = "RANGE_EXPANSION"
                else:
                    h4_bias = "RANGE_CONSOLIDATION"
            elif len(h4_h) >= 15:
                mid = len(h4_h) // 2
                prev_h, curr_h = max(h4_h[:mid]), max(h4_h[mid:])
                prev_l, curr_l = min(h4_l[:mid]), min(h4_l[mid:])
                if curr_h > prev_h and curr_l > prev_l:
                    h4_bias = "BULLISH"
                elif curr_h < prev_h and curr_l < prev_l:
                    h4_bias = "BEARISH"
                elif curr_h > prev_h and curr_l < prev_l:
                    h4_bias = "RANGE_EXPANSION"
                else:
                    h4_bias = "RANGE_CONSOLIDATION"

        if bull_opens >= 3 and h4_bias != "BEARISH":
            overall_bias = "BULLISH"
        elif bull_opens <= 1 and h4_bias != "BULLISH":
            overall_bias = "BEARISH"
        else:
            overall_bias = "RANGE_NEUTRAL"

        return {
            "bias": overall_bias,
            "h4_structure": h4_bias,
            "above_yo": above_yo,
            "above_mo": above_mo,
            "above_wo": above_wo,
            "above_do": above_do,
            "bull_opens_count": bull_opens,
            "desc": (
                f"HTF Bias: {overall_bias} (4H: {h4_bias} | "
                f"Opens: YO {'▲' if above_yo else '▼'} · MO {'▲' if above_mo else '▼'} · "
                f"WO {'▲' if above_wo else '▼'} · DO {'▲' if above_do else '▼'})"
            ),
        }

    @staticmethod
    def evaluate_btc_bias(
        btc_klines_by_tf: Dict[str, Optional[Dict[str, List[float]]]],
        btc_price: float,
        btc_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Xác định thiên hướng dẫn dắt của BTC (Market Leader Bias) thuần Price Action TraderSZ:
        - Không dùng chỉ báo trễ (EMA/RSI/MACD).
        - Ưu tiên 1: BTC đang có tín hiệu SFP / Deviation Return-to-Range (LONG -> BULLISH, SHORT -> BEARISH).
        - Ưu tiên 2: HTF Bias (vị thế so với YO/MO/WO/DO + cấu trúc đỉnh/đáy 4H).
        - Ưu tiên 3: Vị thế nến Intraday so với Daily Open + Weekly Open + cấu trúc 4H.
        """
        if btc_result is None:
            btc_result = TradersszFramework.evaluate_traderssz_complete(
                klines_by_tf=btc_klines_by_tf,
                current_price=btc_price,
            )

        btc_signal = btc_result.get("signal", "WAIT")
        btc_setup = btc_result.get("setup", {}) or {}
        htf_bias = btc_result.get("htf_bias", {}) or {}
        htf_data = btc_result.get("htf_opens_and_ranges", {}) or {}

        h4_struct = htf_bias.get("h4_structure", "NEUTRAL")
        htf_dir = htf_bias.get("bias", "RANGE_NEUTRAL")
        above_do = bool(htf_bias.get("above_do", False))
        above_wo = bool(htf_bias.get("above_wo", False))
        bull_opens = int(htf_bias.get("bull_opens_count", 2))

        do = float(htf_data.get("daily_open", btc_price) or btc_price)
        do_dist_pct = ((btc_price - do) / do * 100.0) if do > 0 else 0.0

        # Kiểm tra thêm lực nến 1H thuần PA của BTC để bắt sớm nhịp pump/dump từ Open & tránh Altcoin Short ngược sóng hồi BTC
        h1_kl = (btc_klines_by_tf or {}).get("1h")
        h1_push = "FLAT"
        intraday_push = "FLAT"
        if h1_kl and len(h1_kl.get("close", [])) >= 3:
            h1_o = float(h1_kl["open"][-1])
            h1_c = float(h1_kl["close"][-1])
            h1_prev_o = float(h1_kl["open"][-2])
            h1_prev_c = float(h1_kl["close"][-2])
            push_2h_pct = ((h1_c - h1_prev_o) / max(h1_prev_o, 1e-9)) * 100.0
            if h1_c > h1_o and btc_price > do:
                h1_push = "UP"
            elif h1_c < h1_o and btc_price < do:
                h1_push = "DOWN"
            if h1_c > h1_o and h1_prev_c >= h1_prev_o and push_2h_pct >= 0.35:
                intraday_push = "UP"
            elif h1_c < h1_o and h1_prev_c <= h1_prev_o and push_2h_pct <= -0.35:
                intraday_push = "DOWN"

        if btc_signal == "LONG":
            bias = "BULLISH"
            strength = "STRONG"
            tf_lbl = str(btc_setup.get("tf", "HTF")).upper()
            rng_lbl = btc_setup.get("range_label", "Range")
            mode_lbl = "Breakout" if btc_setup.get("setup_type") == "RANGE_BREAKOUT_CONTINUATION" else "SFP"
            reason = f"BTC đang kích hoạt LONG {mode_lbl} ({tf_lbl} · {rng_lbl})"
        elif btc_signal == "SHORT":
            bias = "BEARISH"
            strength = "STRONG"
            tf_lbl = str(btc_setup.get("tf", "HTF")).upper()
            rng_lbl = btc_setup.get("range_label", "Range")
            mode_lbl = "Breakout" if btc_setup.get("setup_type") == "RANGE_BREAKOUT_CONTINUATION" else "SFP"
            reason = f"BTC đang kích hoạt SHORT {mode_lbl} ({tf_lbl} · {rng_lbl})"
        elif htf_dir == "BULLISH":
            bias = "BULLISH"
            strength = "MODERATE"
            reason = f"BTC HTF Bias BULLISH (trên {bull_opens}/4 Opens, 4H: {h4_struct})"
        elif htf_dir == "BEARISH":
            bias = "BEARISH"
            strength = "MODERATE"
            reason = f"BTC HTF Bias BEARISH (trên {bull_opens}/4 Opens, 4H: {h4_struct})"
        elif above_do and above_wo and h4_struct != "BEARISH":
            bias = "BULLISH"
            strength = "INTRADAY"
            reason = "BTC giữ giá trên cả Daily Open & Weekly Open"
        elif (not above_do) and (not above_wo) and h4_struct != "BULLISH":
            bias = "BEARISH"
            strength = "INTRADAY"
            reason = "BTC nằm dưới cả Daily Open & Weekly Open"
        elif h4_struct == "BULLISH" and above_do and do_dist_pct >= 0.25 and h1_push == "UP":
            bias = "BULLISH"
            strength = "INTRADAY"
            reason = f"BTC cấu trúc 4H tăng + trên Daily Open (+{do_dist_pct:.2f}%)"
        elif h4_struct == "BEARISH" and (not above_do) and do_dist_pct <= -0.25 and h1_push == "DOWN":
            bias = "BEARISH"
            strength = "INTRADAY"
            reason = f"BTC cấu trúc 4H giảm + dưới Daily Open ({do_dist_pct:.2f}%)"
        else:
            bias = "NEUTRAL"
            strength = "NEUTRAL"
            reason = "BTC đi ngang cân bằng quanh Open/EQ — cho phép Altcoin đánh biên 2 đầu"

        return {
            "bias": bias,
            "strength": strength,
            "btc_signal": btc_signal,
            "btc_price": round(float(btc_price), 2),
            "h4_structure": h4_struct,
            "htf_bias": htf_dir,
            "above_do": above_do,
            "above_wo": above_wo,
            "bull_opens_count": bull_opens,
            "intraday_push": intraday_push,
            "reason": reason,
        }

    @staticmethod
    def evaluate_traderssz_complete(
        klines_by_tf: Dict[str, Optional[Dict[str, List[float]]]],
        current_price: float,
        sl_buffer_pct: float = 0.0065,
        min_rr: float = 2.0,
        btc_bias: Optional[Dict[str, Any]] = None,
        utcnow: Optional[datetime] = None,
        macro_ctx: Optional[Dict[str, Any]] = None,
        rs_ctx: Optional[Dict[str, Any]] = None,
        symbol: str = "",
        asset_type: str = "CRYPTO",
    ) -> Dict[str, Any]:
        """
        Tổng hợp toàn diện hệ thống giao dịch thuần Price Action TraderSZ:
        1. Session Context (Asia / London / NY) + Weekend Liquidity Filter
        2. HTF Opens (YO, MO, WO, DO) & Dealing Ranges (PDH/PDL, Monday Range, PWH/PWL, Monthly, Macro S/R, Asia, Intraday)
        3. 50% Equilibrium & No-Trade Zone Check + Unswept Clean Lows/Highs Magnet Guard
        4. Multi-TF Deviation / SFP & 'Return to Range' Detection (1D, 4H, 1H, 15M) + London Sweep Asia
        5. BTC Market-Leader & Intermarket Confluence (DXY / US10Y Rejection + ALT/BTC Relative Strength)
        6. 3-Step A+ Trigger (Sweep -> Reclaim -> Retest+MSB) & Knife-Catch Small Sizing (0.75x)
        """
        if utcnow is None:
            utcnow = datetime.now(timezone.utc)
        is_weekend = utcnow.weekday() in (5, 6)

        d1_klines  = klines_by_tf.get("1d")
        h4_klines  = klines_by_tf.get("4h")
        h1_klines  = klines_by_tf.get("1h")
        m15_klines = klines_by_tf.get("15m")

        h1_atr_pct = 0.0
        if h1_klines and len(h1_klines.get("close", [])) >= 3:
            _h1_h, _h1_l, _h1_c = h1_klines["high"], h1_klines["low"], h1_klines["close"]
            _h1_bars = [
                (_h1_h[j] - _h1_l[j]) / max(_h1_c[j], 1e-9)
                for j in range(max(0, len(_h1_c) - 11), len(_h1_c) - 1)
                if _h1_c[j] > 0
            ]
            if _h1_bars:
                h1_atr_pct = sum(_h1_bars) / len(_h1_bars)

        session_ctx = TradersszFramework.detect_session_context(utcnow=utcnow)
        htf_data = TradersszFramework.calculate_htf_opens_and_ranges(
            d1_klines=d1_klines,
            h1_klines=h1_klines,
            m15_klines=m15_klines,
            current_price=current_price,
            utcnow=utcnow,
            h4_klines=h4_klines,
        )
        htf_bias = TradersszFramework.evaluate_htf_bias(
            htf_data=htf_data,
            d1_klines=d1_klines,
            h4_klines=h4_klines,
            current_price=current_price,
        )

        # ══════════════════════════════════════════════════════════════════════════
        # KHÓA TUÂN THỦ DEALING RANGE CHÍNH (5 TRẠNG THÁI LOẠI TRỪ LẪN NHAU):
        # 1. Ngoài biên trên (> RH * 1.0020): CHỈ quét LONG Breakout Continuation.
        # 2. Ngoài biên dưới (< RL * 0.9980): CHỈ quét SHORT Breakout Continuation.
        # 3. Dải biên giao thoa sát RH/RL: Đứng ngoài WAIT (tránh nhảy LONG/SHORT tại vạch biên).
        # 4. Vùng giữa NO_TRADE_EQ_ZONE (38% - 62%): BẮT BUỘC WAIT (khóa cả Range phụ).
        # 5. Trong PREMIUM (>= 62%): CHỈ SHORT (SFP + 15m MSB + EQH + Reclaim).
        #    Trong DISCOUNT (<= 38%): CHỈ LONG (SFP + 15m MSB + EQL + Reclaim).
        # ══════════════════════════════════════════════════════════════════════════
        primary_name = str(htf_data.get("range_name", "DAILY_RANGE"))
        primary_zone = str(htf_data.get("market_zone", "NO_TRADE_EQ_ZONE")).upper()
        primary_rh = float(htf_data.get("range_high", current_price) or current_price)
        primary_rl = float(htf_data.get("range_low", current_price) or current_price)
        liq_pools = htf_data.get("liquidity_pools", {}) or {}
        unswept_pools = htf_data.get("unswept_clean_pools", {}) or {}
        monday_rule_state = htf_data.get("monday_rule_state", "STANDARD")

        is_outside_above = current_price > primary_rh * 1.0020
        is_outside_below = current_price < primary_rl * 0.9980
        is_rh_strip = (primary_rh * 0.9995 <= current_price <= primary_rh * 1.0020)
        is_rl_strip = (primary_rl * 0.9980 <= current_price <= primary_rl * 1.0005)

        setups: List[Dict[str, Any]] = []
        clean_pool_blocked: Optional[str] = None

        if is_outside_above or is_outside_below:
            breakout_tfs = [("4h", h4_klines), ("1h", h1_klines), ("15m", m15_klines), ("1d", d1_klines)]
            expected_dir = "LONG" if is_outside_above else "SHORT"
            htf_dir = str(htf_bias.get("bias", "RANGE_NEUTRAL")).upper()
            htf_opposes_bo = (expected_dir == "LONG" and htf_dir == "BEARISH") or (expected_dir == "SHORT" and htf_dir == "BULLISH")
            in_killzone = bool(session_ctx.get("is_killzone", False))
            # Cuối tuần (T7/CN) thanh khoản mỏng: chặn Breakout giả, chỉ giao dịch Range SFP/Deviation chất lượng cao
            if not htf_opposes_bo and not is_weekend:
                for range_obj in htf_data.get("all_ranges", []):
                    if range_obj.get("name") not in ("DAILY_RANGE", "MONDAY_RANGE", "WEEKLY_RANGE", "MONTHLY_RANGE", "SWING_RANGE", "MACRO_SR_RANGE"):
                        continue
                    for tf_name, tf_klines in breakout_tfs:
                        bo_hit = TradersszFramework._scan_tf_breakout(
                            tf_name=tf_name,
                            klines=tf_klines,
                            range_obj=range_obj,
                            current_price=current_price,
                            sl_buffer_pct=max(0.0050, sl_buffer_pct * 0.85),
                            min_rr=min_rr,
                            asset_type=asset_type,
                            h1_atr_pct=h1_atr_pct,
                        )
                        if bo_hit and bo_hit["direction"] == expected_dir:
                            bo_vol = float(bo_hit.get("vol_ratio", 1.0) or 1.0)
                            bo_retest = bool(bo_hit.get("is_retest", False))
                            bo_sub = str(bo_hit.get("sub_mode", ""))
                            # Triết lý 90% thị trường đi ngang: Breakout chỉ là ~10% ngoại lệ ->
                            # trên 15m/1h chỉ nhận Break & Retest hoặc 2-Candle Acceptance kèm Volume >= 1.25x;
                            # ngoài Killzone chỉ nhận Retest hoặc 4H/1D có Volume >= 1.25x.
                            if tf_name in ("15m", "1h") and not bo_retest:
                                if not (bo_sub == "2-Candle Acceptance" and bo_vol >= 1.25):
                                    continue
                            if not in_killzone and not bo_retest:
                                if not (tf_name in ("4h", "1d") and bo_vol >= 1.25):
                                    continue
                            bo_hit["msb_confirmed"] = False
                            bo_hit["liquidity_pool_swept"] = False
                            if range_obj.get("name") == primary_name:
                                bo_hit["score"] += 12
                            setups.append(bo_hit)
        elif is_rh_strip or is_rl_strip or primary_zone == "NO_TRADE_EQ_ZONE":
            setups = []
        else:
            allowed_dir = "LONG" if primary_zone == "DISCOUNT" else ("SHORT" if primary_zone == "PREMIUM" else "")
            if allowed_dir:
                m15_msb = TradersszFramework._check_m15_msb(m15_klines, allowed_dir, current_price)
                is_london = session_ctx.get("session") == "LONDON_OPEN"
                asia_ready = bool(htf_data.get("asia_ready", False))
                asia_h = float(htf_data.get("asia_high", 0) or 0)
                asia_l = float(htf_data.get("asia_low", 0) or 0)
                primary_pos_pct = float(htf_data.get("range_pos_pct", 50.0) or 50.0)
                far_from_daily = bool(htf_data.get("far_from_daily", False))
                MACRO_RANGE_NAMES = ("DAILY_RANGE", "MONDAY_RANGE", "WEEKLY_RANGE", "MONTHLY_RANGE", "MACRO_SR_RANGE")
                is_macro_primary = primary_name in MACRO_RANGE_NAMES
                btc_push = str((btc_bias or {}).get("intraday_push", "FLAT")).upper()
                htf_dir_bias = str(htf_bias.get("bias", "RANGE_NEUTRAL")).upper()
                htf_opposes_dir = (htf_dir_bias == "BULLISH" and allowed_dir == "SHORT") or (htf_dir_bias == "BEARISH" and allowed_dir == "LONG")
                unswept_eqls = [float(x) for x in (unswept_pools.get("all_unswept_eql") or ([unswept_pools["unswept_eql"]] if unswept_pools.get("unswept_eql") else []))]
                unswept_eqhs = [float(x) for x in (unswept_pools.get("all_unswept_eqh") or ([unswept_pools["unswept_eqh"]] if unswept_pools.get("unswept_eqh") else []))]

                # LTF Momentum Veto: Chặn vào SHORT/LONG ngược đầu cây nến 1H lực mạnh hoặc 15m đang ủi sát SL
                ltf_veto = False
                if h1_klines and len(h1_klines.get("close", [])) >= 3:
                    h1_2o, h1_2h, h1_2l, h1_2c = float(h1_klines["open"][-2]), float(h1_klines["high"][-2]), float(h1_klines["low"][-2]), float(h1_klines["close"][-2])
                    h1_2body = abs(h1_2c - h1_2o) / max(h1_2h - h1_2l, 1e-9)
                    h1_3h, h1_3l = float(h1_klines["high"][-3]), float(h1_klines["low"][-3])
                    if allowed_dir == "SHORT" and h1_2c > h1_2o and h1_2body >= 0.48 and h1_2c > h1_3h:
                        ltf_veto = True
                    elif allowed_dir == "LONG" and h1_2c < h1_2o and h1_2body >= 0.48 and h1_2c < h1_3l:
                        ltf_veto = True
                if not ltf_veto and m15_klines and len(m15_klines.get("close", [])) >= 3:
                    m_1o, m_1c = float(m15_klines["open"][-1]), float(m15_klines["close"][-1])
                    m_2o, m_2c = float(m15_klines["open"][-2]), float(m15_klines["close"][-2])
                    if allowed_dir == "SHORT" and m_2c > m_2o and m_1c > m_1o and ((m_1c - m_2o) / max(m_2o, 1e-9) >= 0.0050) and not m15_msb["confirmed"]:
                        ltf_veto = True
                    elif allowed_dir == "LONG" and m_2c < m_2o and m_1c < m_1o and ((m_2o - m_1c) / max(m_2o, 1e-9) >= 0.0050) and not m15_msb["confirmed"]:
                        ltf_veto = True

                scan_tfs = [("1d", d1_klines), ("4h", h4_klines), ("1h", h1_klines), ("15m", m15_klines)]
                if not ltf_veto:
                  for range_obj in htf_data.get("all_ranges", []):
                    r_sub_name = range_obj.get("name", "")
                    is_sub_range = r_sub_name not in MACRO_RANGE_NAMES
                    # Nested Range Guard: Khi giá vẫn còn trong Range Chính, cấm Short/Long sub-range nhỏ ở vùng lửng 21-79%
                    # (Riêng MONTHLY_RANGE và MACRO_SR_RANGE được giữ ngang hàng Macro HTF Range để không bỏ lỡ S/R lớn)
                    if is_macro_primary and not far_from_daily and is_sub_range:
                        if allowed_dir == "SHORT" and primary_pos_pct < 80.0:
                            sub_near_macro = abs(float(range_obj.get("range_high", 0)) - primary_rh) / max(primary_rh, 1e-9) <= 0.006
                            if not sub_near_macro and not (is_london and r_sub_name == "ASIA_RANGE" and m15_msb["confirmed"]):
                                continue
                        elif allowed_dir == "LONG" and primary_pos_pct > 20.0:
                            sub_near_macro = abs(float(range_obj.get("range_low", 0)) - primary_rl) / max(primary_rl, 1e-9) <= 0.006
                            if not sub_near_macro and not (is_london and r_sub_name == "ASIA_RANGE" and m15_msb["confirmed"]):
                                continue

                    for tf_name, tf_klines in scan_tfs:
                        hit = TradersszFramework._scan_tf_deviation(
                            tf_name=tf_name,
                            klines=tf_klines,
                            range_obj=range_obj,
                            current_price=current_price,
                            sl_buffer_pct=sl_buffer_pct,
                            min_rr=min_rr,
                            asset_type=asset_type,
                            h1_atr_pct=h1_atr_pct,
                        )
                        if hit and hit["direction"] == allowed_dir:
                            msb_ok = bool(m15_msb["confirmed"]) and tf_name in ("1d", "4h", "1h", "15m")
                            pool_hit = (allowed_dir == "SHORT" and liq_pools.get("near_rh_eqh")) or (allowed_dir == "LONG" and liq_pools.get("near_rl_eql"))

                            # Bộ lọc nâng Win Rate:
                            # 1) 15m bắt buộc phải có 15m MSB xác nhận (hoặc Retest ngay cụm EQH/EQL) — loại râu nhiễu 15m.
                            if tf_name == "15m" and not (msb_ok or (hit.get("is_retest") and pool_hit)):
                                continue
                            # 2) Range phụ (Asia/Intraday/Swing khi không phải primary) cần 15m MSB hoặc Retest khung lớn 1H/4H/1D.
                            if is_sub_range and r_sub_name != primary_name and not (msb_ok or (hit.get("is_retest") and tf_name in ("1h", "4h", "1d"))):
                                continue
                            # 3) Kèo ngược HTF Bias bắt buộc phải có 15m MSB phá cấu trúc & R:R >= 2.2 (không chặn đầu xu hướng lớn).
                            if htf_opposes_dir and not (msb_ok and float(hit.get("rr_tp2", 0) or 0) >= 2.2):
                                continue
                            # 4) 1H SFP & 4H SFP: cần 15m đồng pha + hợp lưu Retest/EQH/EQL hoặc 15m MSB.
                            if tf_name == "1h" and hit["setup_type"] == "SFP_WICK_REJECTION" and not (msb_ok or (m15_msb["m15_aligned"] and (hit.get("is_retest") or pool_hit))):
                                continue
                            if tf_name == "4h" and m15_msb["available"] and not m15_msb["m15_aligned"] and not hit.get("is_retest") and hit.get("entry_mode") != "LIMIT_RETEST":
                                continue
                            # 5) Chặn Altcoin đánh ngược sóng đẩy ngắn hạn của BTC nếu chưa có 15m MSB xác nhận đảo chiều
                            if ((btc_push == "UP" and allowed_dir == "SHORT") or (btc_push == "DOWN" and allowed_dir == "LONG")) and not msb_ok:
                                continue
                            # 6) TraderSZ Clean Lows / Clean Highs Guard:
                            # Cấm bắt LONG ngay phía trên cụm Clean Lows (EQL) chưa bị quét khi EQL nằm giữa Entry và SL;
                            # Cấm bắt SHORT ngay phía dưới cụm Clean Highs (EQH) chưa bị quét khi EQH nằm giữa Entry và SL.
                            hit_sl = float(hit.get("sl", 0) or 0)
                            hit_sweep_ext = float(hit.get("sweep_extreme", 0) or 0)
                            trap_eql = next(
                                (u for u in unswept_eqls if hit_sl <= u < current_price * 0.9985 and hit_sweep_ext > u * 0.9992),
                                None,
                            )
                            trap_eqh = next(
                                (u for u in unswept_eqhs if current_price * 1.0015 < u <= hit_sl and hit_sweep_ext < u * 1.0008),
                                None,
                            )
                            if allowed_dir == "LONG" and trap_eql is not None:
                                clean_pool_blocked = (
                                    f"🧲 [CLEAN LOWS TRAP] Phát hiện cụm Clean Lows (${trap_eql:.4f}) CHƯA BỊ QUÉT nằm sát dưới Entry — "
                                    f"TraderSZ đứng ngoài chờ quét sạch đáy bằng này trước khi LONG!"
                                )
                                continue
                            elif allowed_dir == "SHORT" and trap_eqh is not None:
                                clean_pool_blocked = (
                                    f"🧲 [CLEAN HIGHS TRAP] Phát hiện cụm Clean Highs (${trap_eqh:.4f}) CHƯA BỊ QUÉT nằm sát trên Entry — "
                                    f"TraderSZ đứng ngoài chờ quét sạch đỉnh bằng này trước khi SHORT!"
                                )
                                continue

                            # Nếu SL của sub-range nằm ngay dưới PDH/trên PDL trong khoảng nam châm 1.8%, nới SL ra ngoài PDH/PDL nếu vẫn giữ được R:R
                            if is_macro_primary and not far_from_daily and is_sub_range and hit.get("entry_mode", "MARKET") == "MARKET":
                                cur_sl = float(hit["sl"])
                                if allowed_dir == "SHORT" and cur_sl <= primary_rh * 1.002 and (primary_rh - current_price) / current_price <= 0.018:
                                    safe_macro_sl = round(primary_rh * (1.0 + sl_buffer_pct), 4)
                                    new_risk = safe_macro_sl - current_price
                                    if new_risk / current_price > 0.048:
                                        continue
                                    new_rr2 = (current_price - float(hit["tp2"])) / max(new_risk, 1e-9)
                                    if new_rr2 < min_rr:
                                        continue
                                    hit["sl"] = safe_macro_sl
                                    hit["sl_pct"] = round(new_risk / current_price * 100.0, 2)
                                    hit["rr_tp1"] = round((current_price - float(hit["tp1"])) / max(new_risk, 1e-9), 2)
                                    hit["rr_tp2"] = round(new_rr2, 2)
                                elif allowed_dir == "LONG" and cur_sl >= primary_rl * 0.998 and (current_price - primary_rl) / current_price <= 0.018:
                                    safe_macro_sl = round(primary_rl * (1.0 - sl_buffer_pct), 4)
                                    new_risk = current_price - safe_macro_sl
                                    if new_risk / current_price > 0.048:
                                        continue
                                    new_rr2 = (float(hit["tp2"]) - current_price) / max(new_risk, 1e-9)
                                    if new_rr2 < min_rr:
                                        continue
                                    hit["sl"] = safe_macro_sl
                                    hit["sl_pct"] = round(new_risk / current_price * 100.0, 2)
                                    hit["rr_tp1"] = round((float(hit["tp1"]) - current_price) / max(new_risk, 1e-9), 2)
                                    hit["rr_tp2"] = round(new_rr2, 2)

                            # Bộ lọc cuối tuần (T7/CN): khung nhỏ 15m/1h phải có 15m MSB hoặc Retest sạch
                            if is_weekend and tf_name in ("15m", "1h") and not msb_ok and not hit.get("is_retest"):
                                continue

                            hit["msb_confirmed"] = msb_ok
                            hit["msb_level"] = m15_msb.get("broken_level", 0.0)
                            if msb_ok:
                                hit["score"] += 10
                                hit["reason"] = f"{hit['reason']} [15m MSB xác nhận @ ${m15_msb['broken_level']:.4f}]"

                            # Kịch bản kinh điển London Sweep Asia Range (Judas Swing 08–13h UTC)
                            swept_lvl = float(hit.get("swept_level", 0) or 0)
                            swept_asia_edge = (
                                asia_ready and swept_lvl > 0 and (
                                    (allowed_dir == "SHORT" and asia_h > 0 and abs(swept_lvl - asia_h) / asia_h <= 0.0045)
                                    or (allowed_dir == "LONG" and asia_l > 0 and abs(swept_lvl - asia_l) / asia_l <= 0.0045)
                                )
                            )
                            london_asia_sweep = bool(is_london and (r_sub_name == "ASIA_RANGE" or swept_asia_edge))
                            hit["is_london_asia_sweep"] = london_asia_sweep
                            if london_asia_sweep:
                                hit["score"] += 10
                                hit["reason"] = f"{hit['reason']} [London Judas Sweep Asia Range -> Mục tiêu biên Asia đối diện]"

                            # Kiểm tra hợp lưu quét cụm thanh khoản EQH / EQL
                            hit["liquidity_pool_swept"] = bool(pool_hit)
                            if pool_hit:
                                hit["score"] += 8
                                pool_tag = "EQH" if allowed_dir == "SHORT" else "EQL"
                                hit["reason"] = f"{hit['reason']} [Quét cụm thanh khoản {pool_tag}]"

                            if r_sub_name == primary_name:
                                hit["score"] += 12
                            setups.append(hit)

                # Quét thêm kèo Reclaim & S/R Flip tại Weekly/Monthly/Daily Open & PDH/PDL/Monday trong vùng hợp lệ
                reclaim_hit = None if ltf_veto else TradersszFramework._scan_level_reclaim(
                    h1_klines=h1_klines,
                    htf_data=htf_data,
                    allowed_dir=allowed_dir,
                    current_price=current_price,
                    sl_buffer_pct=sl_buffer_pct,
                    min_rr=min_rr,
                    asset_type=asset_type,
                    h1_atr_pct=h1_atr_pct,
                )
                if reclaim_hit:
                    if not (m15_msb["confirmed"] or reclaim_hit.get("is_retest")):
                        reclaim_hit = None
                    elif htf_opposes_dir and not m15_msb["confirmed"]:
                        reclaim_hit = None
                    elif is_weekend and not m15_msb["confirmed"] and not reclaim_hit.get("is_retest"):
                        reclaim_hit = None
                    elif ((btc_push == "UP" and allowed_dir == "SHORT") or (btc_push == "DOWN" and allowed_dir == "LONG")) and not m15_msb["confirmed"]:
                        reclaim_hit = None
                if reclaim_hit:
                    r_sl = float(reclaim_hit.get("sl", 0) or 0)
                    r_ext = float(reclaim_hit.get("sweep_extreme", 0) or 0)
                    r_trap_eql = next(
                        (u for u in unswept_eqls if r_sl <= u < current_price * 0.9985 and r_ext > u * 0.9992),
                        None,
                    )
                    r_trap_eqh = next(
                        (u for u in unswept_eqhs if current_price * 1.0015 < u <= r_sl and r_ext < u * 1.0008),
                        None,
                    )
                    if allowed_dir == "LONG" and r_trap_eql is not None:
                        clean_pool_blocked = (
                            f"🧲 [CLEAN LOWS TRAP] Phát hiện cụm Clean Lows (${r_trap_eql:.4f}) CHƯA BỊ QUÉT nằm sát dưới Entry — "
                            f"TraderSZ đứng ngoài chờ quét sạch đáy bằng này trước khi LONG!"
                        )
                        reclaim_hit = None
                    elif allowed_dir == "SHORT" and r_trap_eqh is not None:
                        clean_pool_blocked = (
                            f"🧲 [CLEAN HIGHS TRAP] Phát hiện cụm Clean Highs (${r_trap_eqh:.4f}) CHƯA BỊ QUÉT nằm sát trên Entry — "
                            f"TraderSZ đứng ngoài chờ quét sạch đỉnh bằng này trước khi SHORT!"
                        )
                        reclaim_hit = None
                if reclaim_hit:
                    if m15_msb["confirmed"]:
                        reclaim_hit["msb_confirmed"] = True
                        reclaim_hit["msb_level"] = m15_msb.get("broken_level", 0.0)
                        reclaim_hit["score"] += 8
                    else:
                        reclaim_hit["msb_confirmed"] = False
                    reclaim_hit["liquidity_pool_swept"] = False
                    setups.append(reclaim_hit)

        btc_dir_bias = str((btc_bias or {}).get("bias", "NEUTRAL")).upper()

        if setups:
            candidate_setups = setups
            if btc_dir_bias == "BULLISH":
                aligned_setups = [s for s in setups if s["direction"] == "LONG"]
                if aligned_setups:
                    candidate_setups = aligned_setups
                else:
                    blocked_best = max(setups, key=lambda s: (s["score"], s["rr_tp2"]))
                    eq = htf_data.get("eq_50", current_price)
                    rh = htf_data.get("range_high", current_price)
                    return {
                        "signal": "WAIT",
                        "confidence": 50.0,
                        "setup": {
                            "detected": False,
                            "setup_type": "BTC_ALIGNMENT_BLOCK",
                            "blocked_direction": "SHORT",
                            "blocked_tf": blocked_best.get("tf", ""),
                            "blocked_range": blocked_best.get("range_label", ""),
                        },
                        "all_setups": [],
                        "safe_sl": 0.0,
                        "sl_pct": 0.0,
                        "tp1_target": eq,
                        "tp2_target": rh,
                        "rr_ratio": 0.0,
                        "is_htf_aligned": False,
                        "btc_aligned": False,
                        "btc_bias": btc_bias,
                        "session": session_ctx,
                        "htf_opens_and_ranges": htf_data,
                        "htf_bias": htf_bias,
                        "reason": (
                            f"🛡️ Bỏ qua SHORT ({blocked_best.get('tf','').upper()} {blocked_best.get('range_label','')}) "
                            f"vì ngược chiều BTC: {(btc_bias or {}).get('reason', 'BTC đang BULLISH')}. "
                            f"Tuân thủ đồng pha BTC, chỉ canh LONG khi Altcoin quét đáy Range Low."
                        ),
                    }
            elif btc_dir_bias == "BEARISH":
                aligned_setups = [s for s in setups if s["direction"] == "SHORT"]
                if aligned_setups:
                    candidate_setups = aligned_setups
                else:
                    blocked_best = max(setups, key=lambda s: (s["score"], s["rr_tp2"]))
                    eq = htf_data.get("eq_50", current_price)
                    rl = htf_data.get("range_low", current_price)
                    return {
                        "signal": "WAIT",
                        "confidence": 50.0,
                        "setup": {
                            "detected": False,
                            "setup_type": "BTC_ALIGNMENT_BLOCK",
                            "blocked_direction": "LONG",
                            "blocked_tf": blocked_best.get("tf", ""),
                            "blocked_range": blocked_best.get("range_label", ""),
                        },
                        "all_setups": [],
                        "safe_sl": 0.0,
                        "sl_pct": 0.0,
                        "tp1_target": eq,
                        "tp2_target": rl,
                        "rr_ratio": 0.0,
                        "is_htf_aligned": False,
                        "btc_aligned": False,
                        "btc_bias": btc_bias,
                        "session": session_ctx,
                        "htf_opens_and_ranges": htf_data,
                        "htf_bias": htf_bias,
                        "reason": (
                            f"🛡️ Bỏ qua LONG ({blocked_best.get('tf','').upper()} {blocked_best.get('range_label','')}) "
                            f"vì ngược chiều BTC: {(btc_bias or {}).get('reason', 'BTC đang BEARISH')}. "
                            f"Tuân thủ đồng pha BTC, chỉ canh SHORT khi Altcoin quét đỉnh Range High."
                        ),
                    }

            best = max(candidate_setups, key=lambda s: (s["score"], s["rr_tp2"]))
            direction = best["direction"]

            conf = 70.0
            if best["tf"] in ("1d", "4h"):
                conf += 6.0
            elif best["tf"] == "1h":
                conf += 4.0

            if best["range_name"] in ("MONDAY_RANGE", "DAILY_RANGE", "WEEKLY_RANGE", "MONTHLY_RANGE"):
                conf += 4.0
            elif best["range_name"] in ("SWING_RANGE", "INTRADAY_RANGE", "MACRO_SR_RANGE"):
                conf += 2.0

            if best["range_name"] == "INTRADAY_RANGE" and best["tf"] == "15m" and (best.get("is_retest") or best.get("msb_confirmed")):
                conf += 2.0

            if best.get("is_retest"):
                conf += 3.0

            if best.get("rr_tp2", 0) >= 2.5:
                conf += 3.0

            if best.get("msb_confirmed"):
                conf += 6.0
            else:
                conf -= 4.0

            # 3-Step A+ Trigger (Sweep -> Reclaim -> Retest/FailedBO + 15m MSB)
            is_3step_a_plus = bool(
                best.get("msb_confirmed")
                and best.get("setup_type") != "RANGE_BREAKOUT_CONTINUATION"
                and (best.get("is_retest") or best.get("is_failed_breakout") or float(best.get("vol_ratio", 1.0) or 1.0) >= 1.20)
            )
            best["trigger_tier"] = "A_PLUS_3STEP_TRIGGER" if is_3step_a_plus else "STANDARD_CONFIRMED"
            if is_3step_a_plus:
                conf += 3.0

            if best.get("liquidity_pool_swept"):
                conf += 3.0

            if best.get("is_failed_breakout"):
                conf += 2.0

            if best.get("is_london_asia_sweep"):
                conf += 3.0

            if monday_rule_state == "MONDAY_HUNT_WINDOW" and best["range_name"] == "MONDAY_RANGE":
                conf += 2.0
            elif monday_rule_state == "MONDAY_FORMING":
                conf -= 4.0

            htf_bias_val = str(htf_bias.get("bias", "RANGE_NEUTRAL")).upper()
            htf_opposes_best = (htf_bias_val == "BULLISH" and direction == "SHORT") or (htf_bias_val == "BEARISH" and direction == "LONG")
            if htf_opposes_best:
                conf -= 3.0

            # Hợp lưu Liên thị trường TraderSZ (DXY + US10Y Rejection & ALT/BTC Relative Strength)
            intermarket_info: Dict[str, Any] = {
                "conf_delta": 0.0, "size_mult": 1.0,
                "warnings": [], "aligned": False,
                "intermarket_bias": "NEUTRAL", "summary": "Trung tính",
            }
            if macro_ctx is not None or rs_ctx is not None:
                try:
                    from analyzer.macro_analyzer import evaluate_intermarket_confluence
                    intermarket_info = evaluate_intermarket_confluence(
                        symbol=symbol,
                        asset_type=asset_type,
                        direction=direction,
                        macro_ctx=macro_ctx,
                        btc_ctx=btc_bias,
                        rs_ctx=rs_ctx,
                    )
                    conf += float(intermarket_info.get("conf_delta", intermarket_info.get("conf_adj", 0.0)) or 0.0)
                except Exception:
                    pass

            # TraderSZ Knife-Catch Rule ("When I knife catch, it's small size — get in & get out"):
            opposes_im = bool(intermarket_info.get("warnings") or intermarket_info.get("opposes_macro"))
            is_knife_catch = bool(htf_opposes_best or opposes_im)
            best["is_knife_catch"] = is_knife_catch
            best["scalp_in_and_out"] = is_knife_catch
            knife_catch_size_mult = 0.75 if is_knife_catch else 1.0
            best["intermarket_size_mult"] = round(min(knife_catch_size_mult, float(intermarket_info.get("size_mult", 1.0) or 1.0)), 2)

            wo = htf_data.get("weekly_open", current_price)
            open_aligned = (
                (direction == "LONG" and (current_price >= wo * 0.998 or htf_bias["bias"] == "BULLISH")) or
                (direction == "SHORT" and (current_price <= wo * 1.002 or htf_bias["bias"] == "BEARISH"))
            )
            if open_aligned:
                conf += 3.0

            btc_aligned = (
                (btc_dir_bias == "BULLISH" and direction == "LONG") or
                (btc_dir_bias == "BEARISH" and direction == "SHORT")
            )
            if btc_aligned:
                conf += 2.0

            if session_ctx.get("is_killzone"):
                conf += 2.0

            max_conf_cap = 96.0 if best.get("msb_confirmed") else 80.0
            conf = round(min(max_conf_cap, max(65.0, conf)), 1)
            reason_txt = best["reason"]
            if is_3step_a_plus:
                reason_txt = f"🎯 [3-STEP A+ TRIGGER] {reason_txt}"
            if best.get("is_failed_breakout"):
                reason_txt = f"⚡ [FAILED BREAKOUT REVERSAL] {reason_txt}"
            if best.get("entry_mode") == "LIMIT_RETEST":
                reason_txt = f"⏳ [LIMIT RETEST @ ${best.get('limit_entry', current_price):.4f}] {reason_txt}"
            if is_knife_catch:
                reason_txt = f"{reason_txt} [Knife-Catch ngược sóng HTF/Macro — Size x{best['intermarket_size_mult']} đánh nhanh rút gọn]"
            if monday_rule_state == "MONDAY_FORMING":
                reason_txt = f"{reason_txt} [Thứ Hai đang tạo biên — giảm 25% size]"
            if is_weekend:
                reason_txt = f"{reason_txt} [Lọc thanh khoản cuối tuần]"
            if btc_aligned:
                reason_txt = f"{reason_txt} [Đồng pha BTC {btc_dir_bias}]"
            im_lbl = intermarket_info.get("summary") or intermarket_info.get("label") or ""
            if im_lbl and im_lbl != "Trung tính":
                reason_txt = f"{reason_txt} [Liên thị trường: {im_lbl}]"

            return {
                "signal": direction,
                "confidence": conf,
                "setup": best,
                "all_setups": candidate_setups[:5],
                "safe_sl": best["sl"],
                "sl_pct": best["sl_pct"],
                "tp1_target": best["tp1"],
                "tp2_target": best["tp2"],
                "rr_ratio": best["rr_tp2"],
                "is_htf_aligned": open_aligned,
                "btc_aligned": btc_aligned,
                "btc_bias": btc_bias,
                "intermarket": intermarket_info,
                "session": session_ctx,
                "htf_opens_and_ranges": htf_data,
                "htf_bias": htf_bias,
                "reason": reason_txt,
            }

        # Không có Deviation / SFP hay Breakout xác nhận -> ĐỨNG NGOÀI (WAIT)
        zone = htf_data.get("market_zone", "NO_TRADE_EQ_ZONE")
        rh = htf_data.get("range_high", current_price)
        rl = htf_data.get("range_low", current_price)
        eq = htf_data.get("eq_50", current_price)
        wait_setup_type = "NO_SFP_WAIT"
        if clean_pool_blocked:
            wait_reason = clean_pool_blocked
            wait_conf = 52.0
            wait_setup_type = "CLEAN_POOL_MAGNET_WAIT"
        elif is_outside_above:
            wait_reason = (
                f"⏳ Giá đang phía trên RH (${rh:.4f}): "
                f"Chờ 2 nến đóng giữ vững trên RH để BUY Breakout tiếp diễn, hoặc đóng nến quay lại dưới RH để SHORT SFP."
            )
            wait_conf = 52.0
        elif is_outside_below:
            wait_reason = (
                f"⏳ Giá đang phía dưới RL (${rl:.4f}): "
                f"Chờ 2 nến đóng giữ vững dưới RL để SELL Breakout tiếp diễn, hoặc đóng nến quay lại trên RL để LONG SFP."
            )
            wait_conf = 52.0
        elif zone == "NO_TRADE_EQ_ZONE":
            wait_reason = (
                f"⛔ NO-TRADE ZONE (Giữa Range quanh 50% EQ ${eq:.4f}): "
                f"Giá đang lơ lửng tại {htf_data.get('range_pos_pct', 50)}% của {htf_data.get('range_label')}. "
                f"TraderSZ đứng ngoài chờ quét RH (${rh:.4f}) hoặc RL (${rl:.4f})."
            )
            wait_conf = 45.0
        elif zone == "PREMIUM":
            wait_reason = (
                f"⏳ Vùng PREMIUM ({htf_data.get('range_pos_pct', 70)}% Range): "
                f"Đang theo dõi RH (${rh:.4f}): nếu nến đóng quay lại trong Range -> SHORT; nếu phá vỡ dứt khoát & giữ trên RH -> BUY Breakout tiếp diễn."
            )
            wait_conf = 55.0
        else:
            wait_reason = (
                f"⏳ Vùng DISCOUNT ({htf_data.get('range_pos_pct', 30)}% Range): "
                f"Đang theo dõi RL (${rl:.4f}): nếu nến đóng quay lại trong Range -> LONG; nếu phá vỡ dứt khoát & giữ dưới RL -> SELL Breakout tiếp diễn."
            )
            wait_conf = 55.0

        return {
            "signal": "WAIT",
            "confidence": wait_conf,
            "setup": {"detected": False, "setup_type": wait_setup_type},
            "all_setups": [],
            "safe_sl": 0.0,
            "sl_pct": 0.0,
            "tp1_target": eq,
            "tp2_target": rh if zone == "DISCOUNT" else rl,
            "rr_ratio": 0.0,
            "is_htf_aligned": False,
            "btc_aligned": False,
            "btc_bias": btc_bias,
            "intermarket": {
                "score_adj": 0, "conf_adj": 0.0, "size_mult": 1.0,
                "opposes_macro": False, "rs_vs_btc": float((rs_ctx or {}).get("rs_vs_btc", 0.0) or 0.0),
                "intermarket_bias": str((macro_ctx or {}).get("intermarket_bias", "NEUTRAL")),
                "label": str((macro_ctx or {}).get("summary", "Trung tính")),
            },
            "session": session_ctx,
            "htf_opens_and_ranges": htf_data,
            "htf_bias": htf_bias,
            "reason": wait_reason,
        }
