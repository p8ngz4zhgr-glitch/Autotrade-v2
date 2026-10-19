# 🚀 Autotrade-v2: Scalping M5 & Copy-Trading Bot (v6.2)

Hệ thống Bot giao dịch phái sinh Futures (BingX / Bybit) tích hợp **Chiến Lược Scalping Khung M5**, quản trị rủi ro **Anti-Liquidation Shield (Chống Cháy Tài Khoản 100%)** và bộ tính toán vốn thông minh hướng tới mục tiêu lợi nhuận **20$ - 50$ / ngày**.

---

## 🎯 Bảng Tính Vốn & Kế Hoạch Đạt 20$ - 50$ / Ngày

Chiến lược dựa trên nguyên tắc **Fixed Fractional Risk (1% Risk / Lệnh)** và tỷ lệ **Risk:Reward = 1:2**:

| Vốn Tài Khoản | Rủi ro / Lệnh (1%) | Đòn bẩy an toàn | Ký Quỹ (Margin) | Lãi TP1 (1.5R) | Lãi TP2 (2.5R) | Lãi Trung Bình / Lệnh | Số Lệnh Thắng Để Đạt Target |
|:---|:---|:---|:---|:---|:---|:---|:---|
| **200$** | 2.0$ | 10x - 15x | 20$ - 40$ | +1.8$ | +2.0$ | **+3.8$** | 5 - 6 lệnh (hoặc nâng mục tiêu 15 - 20$/ngày) |
| **500$** | 5.0$ | 10x - 15x | 50$ - 100$ | +4.5$ | +5.0$ | **+9.5$** | **3 - 4 lệnh** để đạt **30$ - 40$/ngày** |
| **1,000$** | 10.0$ | 10x | 100$ - 150$ | +9.0$ | +10.0$ | **+19.0$** | **2 - 3 lệnh** để đạt **40$ - 50$/ngày** |
| **2,000$** | 20.0$ | 5x - 10x | 150$ - 250$ | +18.0$ | +20.0$ | **+38.0$** | **1 - 2 lệnh** để đạt **50$/ngày** |

> 💡 **Quy tắc vàng chốt lệnh:**
> - Khi đạt **TP1 (1.5R)**: Tự động đóng **60%** khối lượng lệnh, đồng thời **dời Stop Loss về Hòa Vốn (Breakeven + bù phí sàn 0.06%)**.
> - Lệnh còn lại **40%** trở thành vị thế **Zero-Risk** thả trôi tới **TP2 (2.5R)**.
> - Khi tổng PnL trong ngày chạm **$20 - $50**: Kích hoạt **Khóa Lợi Nhuận (Target Lock)**, ngắt vào lệnh mới để bảo vệ thành quả.

---

## 🛡️ Cơ Chế Bảo Vệ Chống Cháy Tài Khoản (Anti-Liquidation Shield)

1. **Ngưỡng Cắt Lỗ Cứng (Stop Loss)**: Luôn cách điểm thanh lý của sàn ít nhất **3x đến 5x**.
   - Ví dụ: Cắt lỗ M5 ở mức `0.5%`. Điểm thanh lý của sàn ở mức `5% - 8%`.
   - Giá chạm SL sẽ tự động cắt lỗ nhỏ chỉ mất đúng **1% vốn** (ví dụ 5$ với tài khoản 500$), **tuyệt đối không thể cháy tài khoản**.
2. **Khống Chế Ký Quỹ Tối Đa (Max Margin 25-30%)**: Mỗi lệnh chỉ dùng tối đa 25-30% Equity làm Margin, tài khoản luôn dồi dào sức chịu đựng trước mọi biến động râu quét.
3. **Daily Loss Circuit Breaker**: Khi tổng lỗ trong ngày chạm **-$15** (hoặc 3% tài khoản), Bot **tự động ngừng giao dịch** đến 00:00 UTC hôm sau.
4. **Consecutive Loss Cooldown**: Nếu dính 3 lệnh thua liên tiếp, bot tự động nghỉ **60 phút** để tránh giai đoạn thị trường đi ngang quét râu (choppy/whipsaw).

---

## 💻 Cách Đưa Lên GitHub & Chạy Thực Tế (Render / VPS)

### 1. Đẩy Code Lên GitHub
```bash
git add .
git commit -m "feat: upgrade M5 scalping strategy, anti-liquidation shield & capital sizing"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo-name>.git
git push -u origin main
```

### 2. Cấu Hình Biến Môi Trường (.env)
Sao chép `.env.example` thành `.env` trên VPS hoặc thêm vào Environment Variables trên Render:
```bash
# Telegram & Admin
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
TELEGRAM_ADMIN_CHAT_ID=your_admin_id
ADMIN_SECRET=your_admin_password

# API Keys Phân Tích
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key

# Cấu hình Scalping M5 & Quản Trị Vốn
M5_SCALP_MODE=true
DAILY_PROFIT_TARGET_USD=30.0    # Mục tiêu 20$ - 50$/ngày
DAILY_MAX_LOSS_USD=15.0         # Cắt lỗ tối đa ngày
MAX_RISK_PER_TRADE_PCT=1.0      # Rủi ro 1% / lệnh
MAX_SAFE_LEVERAGE=15            # Đòn bẩy trần an toàn
CONSECUTIVE_LOSS_LIMIT=3        # Giới hạn thua liên tiếp
AUTO_BREAKEVEN_ON_TP1=true      # Tự động dời SL về hòa vốn khi chạm TP1
```

### 3. Chạy Backend Bot Thực Tế
```bash
cd bot_code
pip install -r requirements.txt
python -m uvicorn core_api.main:app --host 0.0.0.0 --port 8000
```

---

## 📊 Giao Diện Dashboard AI Studio
Applet React hiện tại cung cấp:
- Bộ tính toán vốn & đòn bẩy Scalping M5 tương tác trực tiếp
- Sổ lệnh L2 & Phát hiện Bẫy thanh khoản (Liquidity Sweep)
- Mô phỏng quy trình 12 AI Agent thẩm định tín hiệu M5
- Trình xem và sao chép mã nguồn các tệp Python đã tinh chỉnh
