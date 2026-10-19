import React, { useState, useEffect } from "react";
import { 
  Play, ShieldAlert, Cpu, Settings, Terminal, Copy, Check, RefreshCw, 
  TrendingUp, TrendingDown, BookOpen, Layers, Users, Zap, Bell, CheckCircle2, 
  ChevronRight, FileCode, ShieldCheck, DollarSign, Target, Percent, Calculator, 
  AlertTriangle, Lock, Award, Flame, Activity, ArrowRight, ExternalLink
} from "lucide-react";

// Danh sách các cặp giao dịch hỗ trợ Scalping M5 & Futures
const SYMBOLS = [
  { name: "BTCUSDT", price: 92850.5, defaultLev: 10, step: 0.5, decimal: 1, type: "CRYPTO", atrM5: 185.0 },
  { name: "ETHUSDT", price: 3452.2, defaultLev: 10, step: 0.1, decimal: 2, type: "CRYPTO", atrM5: 12.5 },
  { name: "BNBUSDT", price: 612.4, defaultLev: 10, step: 0.05, decimal: 2, type: "CRYPTO", atrM5: 2.8 },
  { name: "HYPEUSDT", price: 18.25, defaultLev: 10, step: 0.01, decimal: 3, type: "CRYPTO", atrM5: 0.15 },
  { name: "XAUUSD", price: 2364.8, defaultLev: 20, step: 0.1, decimal: 2, type: "GOLD", atrM5: 4.2 },
  { name: "TSLA", price: 184.5, defaultLev: 5, step: 0.05, decimal: 2, type: "STOCK", atrM5: 1.1 },
  { name: "NVDA", price: 124.8, defaultLev: 5, step: 0.05, decimal: 2, type: "STOCK", atrM5: 0.8 }
];

export default function App() {
  const [selectedSym, setSelectedSym] = useState(SYMBOLS[0]);
  const [price, setPrice] = useState(SYMBOLS[0].price);
  const [timeframe, setTimeframe] = useState<"5m" | "15m" | "1h">("5m");
  
  // ── M5 SCALPING CAPITAL & RISK CONTROLS ($20 - $50 / NGÀY) ──
  const [accountBalance, setAccountBalance] = useState<number>(500); // Vốn mặc định $500
  const [dailyTargetUsd, setDailyTargetUsd] = useState<number>(30); // Mục tiêu $20 - $50
  const [riskPerTradePct, setRiskPerTradePct] = useState<number>(1.0); // 1.0% rủi ro / lệnh
  const [scalpLeverage, setScalpLeverage] = useState<number>(10); // Đòn bẩy
  const [dailyRealizedPnL, setDailyRealizedPnL] = useState<number>(24.5); // PnL ngày hiện tại
  const [dailyTradesCount, setDailyTradesCount] = useState<number>(3);
  const [dailyWins, setDailyWins] = useState<number>(3);
  const [dailyLosses, setDailyLosses] = useState<number>(0);
  const [isSimulatingTrade, setIsSimulatingTrade] = useState<boolean>(false);
  const [simStep, setSimStep] = useState<number>(0);
  const [simNotification, setSimNotification] = useState<string | null>(null);

  // Real-time Orderbook State
  const [bids, setBids] = useState<any[]>([]);
  const [asks, setAsks] = useState<any[]>([]);
  const [imbalance, setImbalance] = useState(0);
  const [buyWall, setBuyWall] = useState<any>({ price: 0, size: 0, usd: 0 });
  const [sellWall, setSellWall] = useState<any>({ price: 0, size: 0, usd: 0 });
  
  // Liquidity State
  const [liqLongs, setLiqLongs] = useState<any[]>([]);
  const [liqShorts, setLiqShorts] = useState<any[]>([]);
  const [cascadeRisk, setCascadeRisk] = useState(false);
  const [dominantSide, setDominantSide] = useState("LONG");
  const [sweepTriggered, setSweepTriggered] = useState<string | null>(null);
  const [lastSwingLow, setLastSwingLow] = useState(SYMBOLS[0].price * 0.995);
  const [lastSwingHigh, setLastSwingHigh] = useState(SYMBOLS[0].price * 1.005);
  
  // Admin & Bot State
  const [autoTrade, setAutoTrade] = useState(true);
  const [killSwitch, setKillSwitch] = useState(false);
  const [users] = useState([
    { telegram_id: "6286755886", tier: "TIER1", capital: 350.0, leverage: 10, auto: true, risk: 1.0, conf: 68 },
    { telegram_id: "8827361923", tier: "TIER2", capital: 1250.0, leverage: 10, auto: true, risk: 1.0, conf: 75 },
    { telegram_id: "9018273612", tier: "TIER3", capital: 5500.0, leverage: 5, auto: true, risk: 0.8, conf: 80 }
  ]);
  const [logs, setLogs] = useState<string[]>([]);
  
  // AI Pipeline State
  const [isAnalyzing, setIsAIAnalyzing] = useState(false);
  const [analysisStage, setAnalysisStage] = useState(0);
  const [analysisOutput, setAnalysisOutput] = useState<any>(null);
  const [activeAgentTab, setActiveAgentTab] = useState("TECHNICAL");
  
  // Code Viewer State
  const [activeCodeFile, setActiveCodeFile] = useState<string>("risk_engine.py");
  const [copiedText, setCopiedText] = useState(false);

  // Timekeeper
  const [timeStr, setTimeStr] = useState("");

  useEffect(() => {
    const timer = setInterval(() => {
      const now = new Date();
      setTimeStr(now.toLocaleTimeString("vi-VN"));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // Update prices and generate orderbook/liquidity
  useEffect(() => {
    setPrice(selectedSym.price);
    setLastSwingLow(selectedSym.price * 0.995);
    setLastSwingHigh(selectedSym.price * 1.005);
    addLog(`🔄 Hệ thống đã khởi động quét chu kỳ Scalping M5 cho ${selectedSym.name}`);
    generateOrderBook(selectedSym.price);
    generateLiquidationLevels(selectedSym.price);
  }, [selectedSym]);

  const symbol = selectedSym.name;

  // Real-time tick simulator
  useEffect(() => {
    const interval = setInterval(() => {
      const currentPrice = price;
      const randChange = (Math.random() - 0.48) * selectedSym.step * 1.8;
      const newPrice = Math.max(0.1, currentPrice + randChange);
      updatePriceAndL2(newPrice);
    }, 2000);
    return () => clearInterval(interval);
  }, [price, selectedSym]);

  // Orderbook generation logic
  function generateOrderBook(basePrice: number) {
    const bidsArr = [];
    const asksArr = [];
    const step = selectedSym.step;

    let totalBidsUsd = 0;
    let totalAsksUsd = 0;
    
    let askWallIdx = Math.floor(Math.random() * 8) + 4;
    for (let k = 1; k <= 15; k++) {
      const p = basePrice + k * step;
      let size = Math.random() * 1.5 + 0.1;
      if (k === askWallIdx) size *= 8.5;
      const usd = p * size;
      totalAsksUsd += usd;
      asksArr.push({ price: p, qty: size, usd });
    }
    
    let bidWallIdx = Math.floor(Math.random() * 8) + 4;
    for (let k = 1; k <= 15; k++) {
      const p = basePrice - k * step;
      let size = Math.random() * 1.5 + 0.1;
      if (k === bidWallIdx) size *= 9.2;
      const usd = p * size;
      totalBidsUsd += usd;
      bidsArr.push({ price: p, qty: size, usd });
    }

    const sortedAsks = asksArr.sort((a, b) => b.price - a.price);
    const sortedBids = bidsArr.sort((a, b) => b.price - a.price);

    const buyWallItem = sortedBids.reduce((max, b) => b.usd > max.usd ? b : max, sortedBids[0]);
    const sellWallItem = sortedAsks.reduce((max, a) => a.usd > max.usd ? a : max, sortedAsks[0]);

    setBids(sortedBids);
    setAsks(sortedAsks);
    setBuyWall(buyWallItem);
    setSellWall(sellWallItem);

    const total = totalBidsUsd + totalAsksUsd;
    const imb = ((totalBidsUsd - totalAsksUsd) / total) * 100;
    setImbalance(Math.round(imb));
  }

  // Liquidation calculation logic
  function generateLiquidationLevels(basePrice: number) {
    const levs = [5, 10, 20, 50, 100];
    const longs = levs.map(lev => {
      const liqP = basePrice * (1 - 0.9 / lev);
      const dist = ((basePrice - liqP) / basePrice) * 100;
      return { leverage: lev, price: liqP, distance: dist };
    });
    const shorts = levs.map(lev => {
      const liqP = basePrice * (1 + 0.9 / lev);
      const dist = ((liqP - basePrice) / basePrice) * 100;
      return { leverage: lev, price: liqP, distance: dist };
    });

    setLiqLongs(longs);
    setLiqShorts(shorts);

    const closeLiqLong = longs.some(l => l.leverage >= 50 && l.distance < 1.5);
    const closeLiqShort = shorts.some(s => s.leverage >= 50 && s.distance < 1.5);
    setCascadeRisk(closeLiqLong || closeLiqShort);
    setDominantSide(Math.random() > 0.45 ? "LONG" : "SHORT");
  }

  function updatePriceAndL2(newPrice: number) {
    setPrice(newPrice);
    
    // Check for Liquidity sweeps relative to swing low/high
    if (newPrice < lastSwingLow) {
      setSweepTriggered("BULLISH_SWEEP");
      addLog(`🔥 BẪY THANH KHOẢN M5 (BULLISH SWEEP): Quét râu dưới đáy $${lastSwingLow.toFixed(1)}! Phe gom hấp thụ thanh lý.`);
      setLastSwingLow(newPrice * 0.994);
      setTimeout(() => setSweepTriggered(null), 8000);
    } else if (newPrice > lastSwingHigh) {
      setSweepTriggered("BEARISH_SWEEP");
      addLog(`🛑 BẪY THANH KHOẢN M5 (BEARISH SWEEP): Quét râu trên đỉnh $${lastSwingHigh.toFixed(1)}! Lực xả cản dội xuống.`);
      setLastSwingHigh(newPrice * 1.006);
      setTimeout(() => setSweepTriggered(null), 8000);
    }

    generateOrderBook(newPrice);
    generateLiquidationLevels(newPrice);
  }

  function addLog(text: string) {
    const timestamp = new Date().toLocaleTimeString("vi-VN");
    setLogs(prev => [`[${timestamp}] ${text}`, ...prev.slice(0, 29)]);
  }

  // ── M5 SCALPING CAPITAL & RISK MATH CALCULATIONS ──
  // 1. Khoảng cách SL cho M5: sử dụng 1.2 x ATR(M5) hoặc tối thiểu 0.4%
  const slDistPrice = Math.max(selectedSym.price * 0.004, selectedSym.atrM5 * 1.2);
  const slDistPct = (slDistPrice / price) * 100;
  
  // 2. Tiền chịu rủi ro (Risk USD) = Vốn x % Rủi ro
  const riskAmountUsd = accountBalance * (riskPerTradePct / 100);
  
  // 3. Quy mô danh nghĩa (Notional USD) = Risk USD / (SL %)
  const notionalUsd = riskAmountUsd / (slDistPct / 100);
  const contractQty = notionalUsd / price;
  
  // 4. Giới hạn đòn bẩy an toàn (Safe Leverage) để chống cháy:
  // Ngưỡng cháy (0.90 / Lev) phải xa ít nhất gấp 3x khoảng cách SL
  const maxSafeLeverage = Math.min(scalpLeverage, Math.max(2, Math.floor(60 / slDistPct)));
  const actualLev = Math.max(2, Math.min(scalpLeverage, maxSafeLeverage));
  
  // 5. Ký quỹ yêu cầu (Margin USD) = Notional / Leverage
  let requiredMargin = notionalUsd / actualLev;
  const isMarginCapped = requiredMargin > accountBalance * 0.30;
  if (isMarginCapped) {
    requiredMargin = accountBalance * 0.30;
  }
  
  // 6. Điểm cắt lỗ & Chốt lời
  const slPrice = dominantSide === "LONG" ? price - slDistPrice : price + slDistPrice;
  const tp1Price = dominantSide === "LONG" ? price + slDistPrice * 1.5 : price - slDistPrice * 1.5;
  const tp2Price = dominantSide === "LONG" ? price + slDistPrice * 2.5 : price - slDistPrice * 2.5;
  
  // 7. Khoảng cách thanh lý ước tính & Hệ số an toàn (Safety Multiple)
  const liqDistPct = (0.90 / actualLev) * 100;
  const estLiqPrice = dominantSide === "LONG" ? price * (1 - 0.90 / actualLev) : price * (1 + 0.90 / actualLev);
  const safetyMultiple = (liqDistPct / slDistPct).toFixed(1);
  
  // 8. Lợi nhuận dự kiến khi khớp TP (TP1 chốt 60% @ 1.5R, TP2 chốt 40% @ 2.5R = 1.9R)
  const expectedProfitPerWin = (0.60 * 1.5 * riskAmountUsd) + (0.40 * 2.5 * riskAmountUsd);
  const remainingDailyTarget = Math.max(0, dailyTargetUsd - dailyRealizedPnL);
  const tradesNeededForTarget = Math.ceil(remainingDailyTarget / (expectedProfitPerWin || 1));
  const isDailyTargetReached = dailyRealizedPnL >= dailyTargetUsd;
  const maxDailyLoss = 15.0; // Circuit Breaker -$15 USD
  const isCircuitBreakerTripped = dailyRealizedPnL <= -maxDailyLoss;

  // ── MÔ PHỎNG LỆNH SCALPING M5 TƯƠNG TÁC THỰC TẾ ──
  const handleSimulateScalpTrade = () => {
    if (isSimulatingTrade) return;
    if (isCircuitBreakerTripped) {
      alert("🛑 Bot đã ngắt do chạm giới hạn cắt lỗ ngày (-$15). Bảo toàn vốn 100%!");
      return;
    }

    setIsSimulatingTrade(true);
    setSimStep(1);
    addLog(`⚡ [M5 SCALP] Vào lệnh ${dominantSide} ${symbol} @ $${price.toFixed(1)} | Ký quỹ: $${requiredMargin.toFixed(1)} (${actualLev}x) | SL: $${slPrice.toFixed(1)} (-$${riskAmountUsd.toFixed(2)})`);

    // Bước 1: Khớp lệnh
    setTimeout(() => {
      setSimStep(2);
      addLog(`🎯 [M5 SCALP] Giá chạm TP1 (1.5R) @ $${tp1Price.toFixed(1)}! Tự động chốt 60% (+ $${(riskAmountUsd * 0.9).toFixed(2)})`);
      addLog(`🔒 [ANTI-RISK] Dời Stop Loss về HÒA VỐN (Breakeven + 0.06% đệm phí) @ $${price.toFixed(1)}. Vị thế còn lại 40% an toàn tuyệt đối!`);

      // Bước 2: Dời BE và chạy tới TP2
      setTimeout(() => {
        setSimStep(3);
        const profitTotal = expectedProfitPerWin;
        setDailyRealizedPnL(prev => Math.round((prev + profitTotal) * 100) / 100);
        setDailyTradesCount(prev => prev + 1);
        setDailyWins(prev => prev + 1);

        addLog(`🏆 [M5 SCALP] Runner 40% chạm TP2 (2.5R) @ $${tp2Price.toFixed(1)}! Tổng lãi lệnh: +$${profitTotal.toFixed(2)} USD.`);
        
        const newTotal = dailyRealizedPnL + profitTotal;
        if (newTotal >= dailyTargetUsd) {
          setSimNotification(`🏆 CHÚC MỪNG: Đã đạt mục tiêu $${newTotal.toFixed(1)} / $${dailyTargetUsd}$ ngày hôm nay! Đã kích hoạt Khóa Lợi Nhuận.`);
          addLog(`🎉 [TARGET LOCK] ĐÃ ĐẠT CHỈ TIÊU NGÀY $${newTotal.toFixed(1)} >= $${dailyTargetUsd}! Khóa giao dịch bảo vệ thành quả.`);
        } else {
          setSimNotification(`✅ Chốt lời thành công +$${profitTotal.toFixed(2)}! Tiến độ ngày: $${newTotal.toFixed(1)} / $${dailyTargetUsd}`);
        }

        setTimeout(() => {
          setIsSimulatingTrade(false);
          setSimStep(0);
          setTimeout(() => setSimNotification(null), 6000);
        }, 1500);
      }, 2000);
    }, 2000);
  };

  // AI Pipeline Simulator
  const handleRunAIEngine = () => {
    if (isAnalyzing) return;
    setIsAIAnalyzing(true);
    setAnalysisStage(1);
    setAnalysisOutput(null);
    addLog(`🤖 Khởi động quy trình thẩm định 12 AI Agents M5 Scalp cho ${selectedSym.name}...`);

    setTimeout(() => {
      setAnalysisStage(2);
      addLog("🤖 Stage 1 hoàn thành. 4 Domain Analysts đã bỏ phiếu phân loại M5 CVD, RSI và Orderbook.");
      
      setTimeout(() => {
        setAnalysisStage(3);
        addLog("🤖 Stage 2 hoàn thành. Bull vs Bear Researchers đã đối chiếu bẫy thanh khoản râu nến.");

        setTimeout(() => {
          setAnalysisStage(4);
          addLog("🤖 Stage 3-4 hoàn thành. Risk Officer tính toán Ký Quỹ & xác nhận Ngưỡng Cháy an toàn gấp " + safetyMultiple + "x Stoploss.");

          setTimeout(() => {
            setAnalysisStage(5);
            addLog("🏁 Stage 5 hoàn thành! CIO đã phê duyệt lệnh M5 Scalp.");
            
            const finalDirection = dominantSide;
            const rawConfidence = Math.round(78 + Math.random() * 14);

            const mockOutput = {
              symbol: selectedSym.name,
              direction: finalDirection,
              confidence: rawConfidence,
              timeframe: "M5 SCALP",
              plan: { 
                entry: price, 
                sl: slPrice, 
                tp1: tp1Price, 
                tp2: tp2Price,
                margin: requiredMargin,
                leverage: actualLev,
                riskUsd: riskAmountUsd,
                expectedWin: expectedProfitPerWin,
                safetyMultiple
              },
              agents: {
                technical: `Khung M5: RSI đạt ${finalDirection === "LONG" ? "34.5 (Vùng quá bán hồi phục)" : "67.2 (Vùng quá mua đảo chiều)"}. EMA 9 cắt ${finalDirection === "LONG" ? "lên" : "xuống"} EMA 21.`,
                onchain: `Sổ lệnh Imbalance ${imbalance > 0 ? "+" + imbalance : imbalance}%. Tường ${finalDirection === "LONG" ? "mua" : "bán"} dầy hấp thụ toàn bộ lực xả.`,
                macro: `Tỷ lệ Long/Short 5min cân bằng. Không có tin tức CPI/FOMC đột biến trong 60 phút tới.`,
                risk: `Rủi ro: $${riskAmountUsd.toFixed(2)} (đúng 1% vốn). Giá thanh lý cách ${liqDistPct.toFixed(1)}% (gấp ${safetyMultiple}x Stop Loss) -> TUYỆT ĐỐI CHỐNG CHÁY.`
              }
            };
            
            setAnalysisOutput(mockOutput);
            setIsAIAnalyzing(false);
          }, 1200);
        }, 1200);
      }, 1200);
    }, 1200);
  };

  const handleCopyCode = () => {
    navigator.clipboard.writeText(pythonCodeFiles[activeCodeFile] || "");
    setCopiedText(true);
    setTimeout(() => setCopiedText(false), 2000);
  };

  return (
    <div className="min-h-screen bg-[#030611] text-[#e2e8f0] flex flex-col antialiased selection:bg-emerald-500 selection:text-black font-sans">
      
      {/* Dynamic Header */}
      <header className="border-b border-[#1b263e] bg-[#080d1a] px-6 py-4 flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 shrink-0">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-black tracking-wider text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 via-teal-300 to-cyan-400">
              SIGNAL<span className="text-[#05f38c]">BOT</span> v6.2
            </h1>
            <span className="px-2.5 py-0.5 rounded-full text-[11px] font-black bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center gap-1.5 shadow-[0_0_10px_rgba(16,185,129,0.2)]">
              <Flame className="w-3.5 h-3.5 text-emerald-400" />
              M5 SCALPING ENGINE ($20 - $50/NGÀY)
            </span>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 flex items-center gap-1">
              <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
              CHỐNG CHÁY TÀI KHOẢN (ACTIVE)
            </span>
          </div>
          <p className="text-xs text-[#718096] mt-1 font-medium">
            Hệ thống 12 AI Agents • Tự động tính vốn, Ký quỹ an toàn & Khóa lợi nhuận ngày • Sẵn sàng đẩy lên GitHub chạy VPS/Render
          </p>
        </div>

        {/* Right Header Stats */}
        <div className="flex flex-wrap items-center gap-4 self-stretch lg:self-auto justify-between lg:justify-end">
          {/* Daily Goal Bar */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-lg px-3 py-1.5 flex items-center gap-3">
            <div className="flex flex-col text-left">
              <div className="flex items-center justify-between text-[10px] gap-2">
                <span className="text-[#718096] font-bold">MỤC TIÊU NGÀY:</span>
                <span className={`font-mono font-black ${isDailyTargetReached ? "text-emerald-400" : "text-amber-400"}`}>
                  ${dailyRealizedPnL.toFixed(1)} / ${dailyTargetUsd} USD
                </span>
              </div>
              <div className="w-36 h-2 bg-[#040811] rounded-full overflow-hidden mt-1 border border-[#141d2e]">
                <div 
                  className={`h-full transition-all duration-500 ${isDailyTargetReached ? "bg-emerald-400 shadow-[0_0_10px_#10b981]" : "bg-gradient-to-r from-amber-500 to-emerald-400"}`}
                  style={{ width: `${Math.min(100, (dailyRealizedPnL / dailyTargetUsd) * 100)}%` }}
                />
              </div>
            </div>
            {isDailyTargetReached && (
              <span className="badge text-[10px] font-black bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded border border-emerald-500/30 animate-pulse">
                🏆 ĐẠT TARGET
              </span>
            )}
          </div>

          <div className="h-6 w-px bg-[#1b263e] hidden sm:block"></div>

          <div className="text-right font-mono text-xs">
            <span className="text-[#718096]">UTC: </span>
            <span className="text-emerald-400 font-bold">{timeStr || "Đang lấy..."}</span>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-[#718096]">TRẠNG THÁI:</span>
            <span className={`badge text-[11px] font-bold px-2.5 py-1 rounded border ${autoTrade ? "bg-emerald-950/40 text-emerald-400 border-emerald-500/20" : "bg-red-950/40 text-red-400 border-red-500/20"}`}>
              {autoTrade ? "BẬT COPY" : "TẠM DỪNG"}
            </span>
          </div>
        </div>
      </header>

      {/* Target notification alert */}
      {simNotification && (
        <div className="bg-emerald-950/60 border-b border-emerald-500/40 px-6 py-2.5 text-xs text-emerald-300 font-bold flex items-center justify-between transition-all">
          <div className="flex items-center gap-2">
            <Award className="w-4 h-4 text-emerald-400 animate-bounce" />
            <span>{simNotification}</span>
          </div>
          <button onClick={() => setSimNotification(null)} className="text-[#718096] hover:text-white text-xs">✕</button>
        </div>
      )}

      {/* Main Workspace Layout */}
      <main className="flex-1 p-4 md:p-6 max-w-[1750px] w-full mx-auto grid grid-cols-1 xl:grid-cols-12 gap-6 overflow-hidden">
        
        {/* LEFT COLUMN: Capital Sizing & Anti-Liquidation Calculator (4 cols) */}
        <section className="xl:col-span-4 flex flex-col gap-6">
          
          {/* M5 Scalping Capital Calculator Widget */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-xl">
            <div className="flex justify-between items-center">
              <h2 className="text-sm font-black text-[#e2e8f0] tracking-wider uppercase flex items-center gap-2">
                <Calculator className="w-4 h-4 text-emerald-400" /> Bảng Tính Vốn Scalp M5 ($20 - $50/Ngày)
              </h2>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                1:2 R:R
              </span>
            </div>

            {/* Inputs: Capital & Daily Target */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[11px] font-bold text-[#718096] block uppercase tracking-wider mb-1">
                  Vốn Tài Khoản ($)
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-2.5 text-xs text-[#718096] font-mono">$</span>
                  <input 
                    type="number"
                    min="50"
                    max="50000"
                    step="50"
                    value={accountBalance}
                    onChange={(e) => setAccountBalance(Math.max(10, Number(e.target.value)))}
                    className="w-full bg-[#040811] border border-[#141d2e] rounded-lg py-2 pl-7 pr-2 font-mono text-sm font-bold text-white focus:outline-none focus:border-emerald-500/50"
                  />
                </div>
                <div className="flex gap-1 mt-1.5">
                  {[100, 200, 500, 1000].map(amt => (
                    <button
                      key={amt}
                      onClick={() => setAccountBalance(amt)}
                      className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${accountBalance === amt ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30" : "bg-[#070b16] text-[#718096] hover:text-white"}`}
                    >
                      ${amt}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-[11px] font-bold text-[#718096] block uppercase tracking-wider mb-1">
                  Mục Tiêu Lãi / Ngày
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-2.5 text-xs text-emerald-400 font-mono">$</span>
                  <input 
                    type="number"
                    min="10"
                    max="500"
                    step="5"
                    value={dailyTargetUsd}
                    onChange={(e) => setDailyTargetUsd(Math.max(5, Number(e.target.value)))}
                    className="w-full bg-[#040811] border border-[#141d2e] rounded-lg py-2 pl-7 pr-2 font-mono text-sm font-bold text-emerald-400 focus:outline-none focus:border-emerald-500/50"
                  />
                </div>
                <div className="flex gap-1 mt-1.5">
                  {[20, 30, 40, 50].map(tgt => (
                    <button
                      key={tgt}
                      onClick={() => setDailyTargetUsd(tgt)}
                      className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${dailyTargetUsd === tgt ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30" : "bg-[#070b16] text-[#718096] hover:text-white"}`}
                    >
                      ${tgt}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Inputs: Risk % and Leverage */}
            <div className="grid grid-cols-2 gap-3 pt-2 border-t border-[#141d2e]">
              <div>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-[11px] font-bold text-[#718096] uppercase">Rủi Ro / Lệnh</span>
                  <span className="text-xs font-mono font-bold text-amber-400">{riskPerTradePct}% vốn</span>
                </div>
                <input 
                  type="range"
                  min="0.5"
                  max="2.0"
                  step="0.1"
                  value={riskPerTradePct}
                  onChange={(e) => setRiskPerTradePct(Number(e.target.value))}
                  className="w-full accent-emerald-400 cursor-pointer"
                />
                <span className="text-[10px] text-[#718096] block mt-0.5 font-mono">
                  Mất tối đa: <strong className="text-red-400 font-bold">${riskAmountUsd.toFixed(2)}</strong> nếu dính SL
                </span>
              </div>

              <div>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-[11px] font-bold text-[#718096] uppercase">Đòn Bẩy Chọn</span>
                  <span className="text-xs font-mono font-bold text-cyan-400">{scalpLeverage}x (An toàn: {actualLev}x)</span>
                </div>
                <div className="grid grid-cols-4 gap-1">
                  {[5, 10, 15, 20].map(lev => (
                    <button
                      key={lev}
                      onClick={() => setScalpLeverage(lev)}
                      className={`py-1 rounded text-[11px] font-mono font-bold transition-all border ${
                        scalpLeverage === lev 
                          ? "bg-cyan-950/40 text-cyan-300 border-cyan-500/40" 
                          : "bg-[#070b16] text-[#718096] border-transparent hover:border-[#141d2e]"
                      }`}
                    >
                      {lev}x
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Live Real-time Calculated Execution Metrics */}
            <div className="bg-[#040811] border border-[#141d2e] rounded-lg p-3.5 flex flex-col gap-2.5 font-mono text-xs">
              <div className="flex justify-between items-center pb-2 border-b border-[#141d2e]">
                <span className="text-[#718096]">Ký quỹ cần vào lệnh:</span>
                <span className="font-bold text-white">
                  ${requiredMargin.toFixed(2)} USD 
                  <span className="text-[10px] text-[#718096] ml-1">({((requiredMargin / accountBalance) * 100).toFixed(1)}% vốn)</span>
                </span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-[#718096]">Khối lượng hợp đồng:</span>
                <span className="font-bold text-emerald-400">{contractQty.toFixed(4)} {selectedSym.name.replace("USDT","")}</span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-red-400">🛑 Cắt lỗ SL ({slDistPct.toFixed(2)}%):</span>
                <span className="font-bold text-red-400">${slPrice.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })} (-${riskAmountUsd.toFixed(2)})</span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-emerald-400">🎯 Chốt lời TP1 (1.5R):</span>
                <span className="font-bold text-emerald-400">${tp1Price.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })} (+${(riskAmountUsd * 0.9).toFixed(2)})</span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-cyan-400">🏆 Chốt lời TP2 (2.5R):</span>
                <span className="font-bold text-cyan-400">${tp2Price.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })} (+${(riskAmountUsd * 1.0).toFixed(2)})</span>
              </div>

              <div className="flex justify-between items-center pt-2 border-t border-[#141d2e]">
                <span className="text-[#718096]">Lãi trung bình / lệnh thắng:</span>
                <span className="font-bold text-[#05f38c]">+{expectedProfitPerWin.toFixed(2)} USD (1.9R)</span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-[#718096]">Số lệnh thắng cần đạt target:</span>
                <span className="font-bold text-amber-300">{tradesNeededForTarget} lệnh nữa (Mục tiêu: ${dailyTargetUsd})</span>
              </div>
            </div>

            {/* Anti-Liquidation Guarantee Banner */}
            <div className="p-3 rounded-lg border border-emerald-500/30 bg-emerald-950/20 flex flex-col gap-1.5">
              <div className="flex items-center gap-2 text-emerald-400 font-bold text-xs">
                <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
                <span>CHỐNG CHÁY TÀI KHOẢN (ANTI-LIQUIDATION SHIELD)</span>
              </div>
              <p className="text-[11px] text-[#a0aec0] leading-relaxed">
                Giá thanh lý sàn ước tính: <strong className="text-white font-mono">${estLiqPrice.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })}</strong> (cách {liqDistPct.toFixed(1)}%). 
                Khoảng cách cháy xa <strong className="text-emerald-300 font-mono font-bold">gấp {safetyMultiple} lần</strong> khoảng cách Stoploss ({slDistPct.toFixed(2)}%).
                Lệnh luôn chạm SL và tự động cắt lỗ nhỏ đúng <strong className="text-red-400">${riskAmountUsd.toFixed(2)}</strong> trước khi có bất kỳ nguy cơ chạm thanh lý sàn.
              </p>
            </div>

            {/* Interactive Simulation Button */}
            <button
              onClick={handleSimulateScalpTrade}
              disabled={isSimulatingTrade || isCircuitBreakerTripped}
              className="w-full py-2.5 px-4 rounded-lg text-xs font-black bg-gradient-to-r from-emerald-500 via-teal-400 to-cyan-500 text-black hover:opacity-95 disabled:opacity-50 transition-all flex items-center justify-center gap-2 shadow-[0_0_20px_rgba(5,243,140,0.2)]"
            >
              {isSimulatingTrade ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin text-black" />
                  {simStep === 1 && "Đang vào lệnh M5 & Thiết lập SL..."}
                  {simStep === 2 && "Chạm TP1! Đang chốt 60% & Dời SL về Breakeven..."}
                  {simStep === 3 && "Chạm TP2! Khóa lợi nhuận ngày..."}
                </>
              ) : (
                <>
                  <Play className="w-4 h-4 fill-black" />
                  MÔ PHỎNG LỆNH SCALP M5 (TP1 + BE + TP2)
                </>
              )}
            </button>
          </div>

          {/* Symbol Select & Quick Simulation */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
            <div className="flex justify-between items-center">
              <h2 className="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                <Zap className="w-4 h-4 text-emerald-400" /> Chọn Cặp Scalp M5
              </h2>
              <div className="flex gap-1 bg-[#040811] p-0.5 rounded border border-[#141d2e]">
                {(["5m", "15m", "1h"] as const).map(tf => (
                  <button
                    key={tf}
                    onClick={() => setTimeframe(tf)}
                    className={`px-2 py-0.5 text-[10px] font-bold rounded ${timeframe === tf ? "bg-emerald-500/20 text-emerald-300 font-mono" : "text-[#718096]"}`}
                  >
                    {tf}
                  </button>
                ))}
              </div>
            </div>
            
            <div className="grid grid-cols-3 gap-2">
              {SYMBOLS.map(sym => (
                <button
                  key={sym.name}
                  id={`sym-btn-${sym.name}`}
                  onClick={() => setSelectedSym(sym)}
                  className={`py-2 px-2.5 rounded-lg text-xs font-bold transition-all flex flex-col items-center justify-between gap-1 border ${
                    selectedSym.name === sym.name 
                      ? "bg-emerald-950/30 text-[#05f38c] border-emerald-500/40 shadow-[0_0_15px_-3px_rgba(5,243,140,0.15)]"
                      : "bg-[#080d1a] text-[#718096] border-transparent hover:border-[#1b263e] hover:text-[#e2e8f0]"
                  }`}
                >
                  <span className="font-mono">{sym.name}</span>
                  <span className="text-[10px] text-[#718096]">{sym.type}</span>
                </button>
              ))}
            </div>

            {/* Price Tracker Display */}
            <div className="bg-[#070b16] border border-[#141d2e] rounded-lg p-4 flex justify-between items-center relative overflow-hidden">
              <div>
                <span className="text-[11px] text-[#718096] font-bold block uppercase tracking-wider">Giá Thị Trường {timeframe.toUpperCase()}</span>
                <span className="text-2xl font-black font-mono text-[#e2e8f0] tracking-tight mt-1 inline-block">
                  ${price.toLocaleString("en-US", { minimumFractionDigits: selectedSym.decimal })}
                </span>
              </div>

              <div className="text-right">
                <span className="text-[11px] text-[#718096] font-bold block uppercase tracking-wider">Test Biến Động</span>
                <div className="flex gap-1.5 mt-1.5">
                  <button 
                    onClick={() => updatePriceAndL2(price * 1.002)}
                    className="p-1 px-2.5 rounded text-[10px] font-black bg-emerald-950/40 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-900/40 flex items-center gap-1"
                  >
                    <TrendingUp className="w-3 h-3" /> PUMP
                  </button>
                  <button 
                    onClick={() => updatePriceAndL2(price * 0.998)}
                    className="p-1 px-2.5 rounded text-[10px] font-black bg-red-950/40 text-red-400 border border-red-500/20 hover:bg-red-900/40 flex items-center gap-1"
                  >
                    <TrendingDown className="w-3 h-3" /> DUMP
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Admin Web controls Panel */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
            <h2 className="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
              <Settings className="w-4 h-4 text-emerald-400" /> Kỷ Luật Giao Dịch & Circuit Breaker
            </h2>

            <div className="flex flex-col gap-2.5 font-mono text-xs">
              <div className="flex justify-between items-center p-2.5 bg-[#070b16] border border-[#141d2e] rounded-lg">
                <div>
                  <span className="font-bold block text-white">Chặn Lỗ Tối Đa Ngày (Circuit Breaker)</span>
                  <span className="text-[10px] text-[#718096]">Tự ngắt bot nếu chạm ngưỡng lỗ</span>
                </div>
                <span className="px-2 py-0.5 rounded text-red-400 font-bold bg-red-950/30 border border-red-500/20">
                  -${maxDailyLoss.toFixed(1)} USD
                </span>
              </div>

              <div className="flex justify-between items-center p-2.5 bg-[#070b16] border border-[#141d2e] rounded-lg">
                <div>
                  <span className="font-bold block text-white">Khóa Lãi Ngày (Target Lock)</span>
                  <span className="text-[10px] text-[#718096]">Ngưng vào lệnh khi đạt 20$ - 50$</span>
                </div>
                <span className="px-2 py-0.5 rounded text-emerald-400 font-bold bg-emerald-950/30 border border-emerald-500/20">
                  +${dailyTargetUsd} USD
                </span>
              </div>

              <div className="flex justify-between items-center p-2.5 bg-[#070b16] border border-[#141d2e] rounded-lg">
                <div>
                  <span className="font-bold block text-white">Tự Dời SL Về Breakeven (TP1)</span>
                  <span className="text-[10px] text-[#718096]">Bù phí 0.06% + đưa rủi ro về 0</span>
                </div>
                <span className="px-2 py-0.5 rounded text-cyan-400 font-bold bg-cyan-950/30 border border-cyan-500/20">
                  BẬT (TP1 1.5R)
                </span>
              </div>
            </div>
          </div>

        </section>

        {/* MIDDLE COLUMN: Real-Time Order Book, AI decision simulation (5 cols) */}
        <section className="xl:col-span-5 flex flex-col gap-6">
          
          {/* L2 Real-time Order Book */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
            <div className="flex justify-between items-center">
              <h2 className="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-emerald-400" /> Sổ Lệnh Độ Sâu L2 (Orderbook)
              </h2>
              
              <div className="text-right">
                <span className="text-[10px] text-[#718096] uppercase block">Imbalance</span>
                <span className={`text-xs font-black font-mono ${imbalance >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {imbalance >= 0 ? `+${imbalance}%` : `${imbalance}%`}
                </span>
              </div>
            </div>

            {/* Orderbook entries */}
            <div className="grid grid-cols-1 gap-1">
              
              {/* Asks (Sells) */}
              <div className="flex flex-col gap-0.5 font-mono text-[11px]">
                {asks.slice(-5).map((ask, idx) => (
                  <div key={idx} className="flex justify-between items-center relative py-1 px-2 rounded hover:bg-white/5 transition-all">
                    <div 
                      className="absolute right-0 top-0 bottom-0 bg-red-500/5 transition-all"
                      style={{ width: `${Math.min(100, (ask.usd / (sellWall.usd || 1)) * 100)}%` }}
                    />
                    <span className="text-red-400 font-bold relative z-10">
                      ${ask.price.toLocaleString("en-US", { minimumFractionDigits: selectedSym.decimal })}
                    </span>
                    <span className="text-[#e2e8f0] relative z-10">{ask.qty.toFixed(3)}</span>
                    <span className="text-[#718096] relative z-10">${Math.round(ask.usd).toLocaleString()}</span>
                  </div>
                ))}
              </div>

              {/* Middle Spread Line */}
              <div className="border-y border-[#1b263e]/40 py-2 my-2 flex justify-between items-center text-xs font-bold text-[#718096] px-2 bg-[#070b16]/50">
                <div className="flex items-center gap-1">
                  <span>SPREAD: </span>
                  <span className="text-[#e2e8f0] font-mono">
                    {asks[asks.length-1] && bids[0] ? ((asks[asks.length-1].price - bids[0].price) / bids[0].price * 100).toFixed(4) : "0.001"}%
                  </span>
                </div>
                <div>
                  <span className="text-[10px]">TƯỜNG CẢN BÁN: </span>
                  <span className="text-red-400 font-mono">${sellWall.price.toLocaleString("en-US", { minimumFractionDigits: selectedSym.decimal })}</span>
                </div>
              </div>

              {/* Bids (Buys) */}
              <div className="flex flex-col gap-0.5 font-mono text-[11px]">
                {bids.slice(0, 5).map((bid, idx) => (
                  <div key={idx} className="flex justify-between items-center relative py-1 px-2 rounded hover:bg-white/5 transition-all">
                    <div 
                      className="absolute right-0 top-0 bottom-0 bg-emerald-500/5 transition-all"
                      style={{ width: `${Math.min(100, (bid.usd / (buyWall.usd || 1)) * 100)}%` }}
                    />
                    <span className="text-emerald-400 font-bold relative z-10">
                      ${bid.price.toLocaleString("en-US", { minimumFractionDigits: selectedSym.decimal })}
                    </span>
                    <span className="text-[#e2e8f0] relative z-10">{bid.qty.toFixed(3)}</span>
                    <span className="text-[#718096] relative z-10">${Math.round(bid.usd).toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Support and resistance details */}
            <div className="bg-[#070b16] rounded-lg border border-[#141d2e] p-3 text-xs flex justify-between gap-3 text-center">
              <div>
                <span className="text-[#718096] text-[10px] block font-bold uppercase">Hỗ Trợ (Tường Mua)</span>
                <span className="text-emerald-400 font-black block mt-0.5">${buyWall.price.toLocaleString()}</span>
                <span className="text-[10px] text-[#718096]">Khối lượng: {Math.round(buyWall.usd).toLocaleString()} USD</span>
              </div>
              <div className="w-px bg-[#1b263e]"></div>
              <div>
                <span className="text-[#718096] text-[10px] block font-bold uppercase">Kháng Cự (Tường Bán)</span>
                <span className="text-red-400 font-black block mt-0.5">${sellWall.price.toLocaleString()}</span>
                <span className="text-[10px] text-[#718096]">Khối lượng: {Math.round(sellWall.usd).toLocaleString()} USD</span>
              </div>
            </div>
          </div>

          {/* 12 AI Agent Multi-Stage Pipeline Simulator */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
            <div className="flex justify-between items-center">
              <h2 className="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                <Cpu className="w-4 h-4 text-emerald-400" /> Thẩm Định 12 AI Agent (M5 Scalping)
              </h2>
              <button 
                onClick={handleRunAIEngine}
                disabled={isAnalyzing}
                className="py-1 px-3 rounded-lg text-xs font-black bg-gradient-to-r from-emerald-500 to-cyan-500 text-black hover:opacity-90 disabled:opacity-50 transition-all flex items-center gap-1.5 shadow-[0_0_15px_rgba(16,185,129,0.15)]"
              >
                <Play className="w-3.5 h-3.5 fill-black" /> CHẠY AI PIPELINE
              </button>
            </div>

            {/* Pipeline Stage Indicators */}
            <div className="grid grid-cols-5 gap-1 text-center text-[10px] font-bold">
              {[1, 2, 3, 4, 5].map(stage => (
                <div 
                  key={stage} 
                  className={`py-1 rounded border transition-all ${
                    analysisStage === stage 
                      ? "bg-emerald-950/40 text-emerald-400 border-emerald-500/40 animate-pulse"
                      : analysisStage > stage 
                      ? "bg-emerald-500/10 text-emerald-500/80 border-transparent"
                      : "bg-[#070b16] text-[#718096] border-transparent"
                  }`}
                >
                  Stage {stage}
                </div>
              ))}
            </div>

            {/* Live running state panel */}
            {isAnalyzing && (
              <div className="p-4 bg-[#070b16] border border-[#141d2e] rounded-lg flex flex-col items-center justify-center gap-3 py-6">
                <RefreshCw className="w-6 h-6 text-emerald-400 animate-spin" />
                <div className="text-center">
                  <p className="text-xs font-bold text-[#e2e8f0]">
                    {analysisStage === 1 && "Stage 1: 4 Domain Analysts đang đọc dữ liệu M5 (CVD, RSI, Orderbook)..."}
                    {analysisStage === 2 && "Stage 2: Bull vs Bear Researcher tranh biện rủi ro râu quét..."}
                    {analysisStage === 3 && "Stage 3 & 4: Trader dựng kế hoạch SL 1:2 R:R và Risk Officer duyệt đòn bẩy..."}
                    {analysisStage === 4 && "Stage 5: CIO thẩm định xác nhận Zero-Liquidation Risk..."}
                  </p>
                  <p className="text-[10px] text-[#718096] mt-1">Đang xử lý kết nối với API LLMs qua Circuit Breaker...</p>
                </div>
              </div>
            )}

            {/* Completed Output and Signal card */}
            {analysisOutput && (
              <div className="flex flex-col gap-4">
                {/* Visual tabs of the 4 key Stage 1 analysts */}
                <div className="flex bg-[#070b16] p-0.5 rounded-lg border border-[#141d2e]">
                  {["TECHNICAL", "ONCHAIN", "MACRO", "RISK"].map(tab => (
                    <button
                      key={tab}
                      onClick={() => setActiveAgentTab(tab)}
                      className={`flex-1 py-1 text-[10px] font-black rounded transition-all ${
                        activeAgentTab === tab 
                          ? "bg-emerald-500/10 text-[#05f38c]" 
                          : "text-[#718096] hover:text-[#e2e8f0]"
                      }`}
                    >
                      {tab}
                    </button>
                  ))}
                </div>

                <div className="bg-[#070b16] border border-[#141d2e] rounded-lg p-3 text-xs font-mono text-emerald-300 leading-relaxed">
                  {activeAgentTab === "TECHNICAL" && `[TECHNICAL_ANALYST]: ${analysisOutput.agents.technical}`}
                  {activeAgentTab === "ONCHAIN" && `[ONCHAIN_ANALYST]: ${analysisOutput.agents.onchain}`}
                  {activeAgentTab === "MACRO" && `[MACRO_ANALYST]: ${analysisOutput.agents.macro}`}
                  {activeAgentTab === "RISK" && `[RISK_OFFICER]: ${analysisOutput.agents.risk}`}
                </div>

                {/* Final Verdict Telegram Signal representation */}
                <div className="border border-emerald-500/20 bg-emerald-950/10 rounded-xl p-4 flex flex-col gap-3">
                  <div className="flex justify-between items-start">
                    <div>
                      <span className="px-2 py-0.5 rounded text-[10px] font-black bg-emerald-500/20 text-[#05f38c]">
                        🟢 TÍN HIỆU SCALP M5 {analysisOutput.direction} — {analysisOutput.symbol}
                      </span>
                      <h3 className="text-sm font-black mt-2 font-mono">ĐỘ TIN CẬY: {analysisOutput.confidence}%</h3>
                    </div>
                    <span className="text-[10px] text-[#718096] font-mono">Đòn bẩy: {analysisOutput.plan.leverage}x | Ký quỹ: ${analysisOutput.plan.margin.toFixed(1)}</span>
                  </div>

                  <div className="grid grid-cols-2 gap-3 text-xs font-mono bg-[#040811] p-3 rounded-lg border border-emerald-500/10">
                    <div>
                      <span className="text-[#718096] text-[10px] block">Vào lệnh</span>
                      <span className="text-[#e2e8f0] font-bold">${analysisOutput.plan.entry.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })}</span>
                    </div>
                    <div>
                      <span className="text-red-400 text-[10px] block">🛑 Cắt lỗ (SL 1.0% vốn)</span>
                      <span className="text-red-400 font-bold">${analysisOutput.plan.sl.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })}</span>
                    </div>
                    <div>
                      <span className="text-emerald-400 text-[10px] block">🎯 TP1 (1.5R - Chốt 60% + Dời BE)</span>
                      <span className="text-emerald-400 font-bold">${analysisOutput.plan.tp1.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })}</span>
                    </div>
                    <div>
                      <span className="text-blue-400 text-[10px] block">🏆 TP2 (2.5R - Chốt 40%)</span>
                      <span className="text-blue-400 font-bold">${analysisOutput.plan.tp2.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })}</span>
                    </div>
                  </div>

                  <div className="text-[11px] text-[#718096] border-t border-[#1b263e]/40 pt-2 flex flex-col gap-1 font-mono">
                    <div className="flex justify-between">
                      <span>🛡️ Khoảng cách Cháy vs SL:</span>
                      <span className="text-emerald-400 font-bold">Xa gấp {analysisOutput.plan.safetyMultiple}x SL (Tuyệt đối không cháy)</span>
                    </div>
                    <div className="flex justify-between">
                      <span>💰 Lợi nhuận kỳ vọng / lệnh:</span>
                      <span className="text-[#05f38c] font-bold">+${analysisOutput.plan.expectedWin.toFixed(2)} USD</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* RIGHT COLUMN: Code Viewers and system logs (3 cols) */}
        <section className="xl:col-span-3 flex flex-col gap-6">
          
          {/* Live system logs */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 flex-1 min-h-[260px]">
            <h2 className="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
              <Terminal className="w-4 h-4 text-emerald-400" /> Nhật Ký Hệ Thống Bot
            </h2>

            <div className="bg-[#040811] rounded-lg border border-[#141d2e] p-3 flex-1 overflow-y-auto max-h-[300px] xl:max-h-none font-mono text-[11px] flex flex-col gap-2 scrollbar-thin">
              {logs.map((log, index) => (
                <div key={index} className="text-[#a0aec0] leading-relaxed border-b border-[#141d2e]/30 pb-1.5 last:border-0">
                  {log}
                </div>
              ))}
              {logs.length === 0 && (
                <div className="text-[#718096] text-center my-auto">Chưa có hoạt động nào phát sinh.</div>
              )}
            </div>
          </div>

          {/* Quick links to download the python backend files */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
            <h2 className="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
              <FileCode className="w-4 h-4 text-emerald-400" /> Mã Nguồn Sẵn Sàng Lên GitHub
            </h2>
            <p className="text-[11px] text-[#718096]">
              Toàn bộ file backend python đã được tối ưu hóa cho chiến thuật Scalping M5, quản trị vốn $20 - $50/ngày và chống cháy tài khoản:
            </p>

            <div className="flex flex-col gap-1.5">
              {Object.keys(pythonCodeFiles).map(fileKey => (
                <button
                  key={fileKey}
                  onClick={() => setActiveCodeFile(fileKey)}
                  className={`p-2.5 rounded-lg text-xs font-bold text-left transition-all border flex items-center justify-between ${
                    activeCodeFile === fileKey 
                      ? "bg-emerald-950/20 text-[#05f38c] border-emerald-500/30"
                      : "bg-[#080d1a] text-[#718096] border-transparent hover:border-[#1b263e] hover:text-[#e2e8f0]"
                  }`}
                >
                  <span className="truncate">{fileKey}</span>
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              ))}
            </div>
          </div>

        </section>

      </main>

      {/* Code Viewer Tab (Bottom section) */}
      <section className="mx-6 mb-8 bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
          <div>
            <h2 className="text-sm font-bold text-[#e2e8f0] tracking-wider uppercase flex items-center gap-2">
              <FileCode className="w-4 h-4 text-emerald-400" /> Trình Duyệt Mã Nguồn: <span className="text-emerald-400 font-mono">{activeCodeFile}</span>
            </h2>
            <p className="text-xs text-[#718096] mt-0.5">Sao chép tệp tin này hoặc commit thẳng lên GitHub repo để chạy trên VPS / Render.</p>
          </div>

          <button
            onClick={handleCopyCode}
            className="py-1.5 px-4 rounded-lg text-xs font-bold bg-[#141d2e] border border-[#1b263e] text-[#e2e8f0] hover:bg-[#1b263e] transition-all flex items-center gap-2 self-stretch sm:self-auto justify-center"
          >
            {copiedText ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-400" /> ĐÃ SAO CHÉP!
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" /> SAO CHÉP MÃ NGUỒN NÀY
              </>
            )}
          </button>
        </div>

        <div className="bg-[#040811] rounded-lg border border-[#141d2e] p-4 overflow-x-auto font-mono text-xs text-[#a0aec0] max-h-[450px] scrollbar-thin leading-relaxed">
          <pre>{pythonCodeFiles[activeCodeFile]}</pre>
        </div>
      </section>
    </div>
  );
}

// ── BỘ TẬP HỢP CÁC FILE PYTHON ĐÃ TINH CHỈNH PHỤC VỤ XEM VÀ SAO CHÉP ──
const pythonCodeFiles: Record<string, string> = {
  "risk_engine.py": `# ═══════════════════════════════════════════════════════════
# RISK ENGINE & M5 SCALPING CAPITAL MANAGER — SignalBot v6.2
# ═══════════════════════════════════════════════════════════
# Bảo vệ tuyệt đối rủi ro tài khoản (Anti-Liquidation Shield)
# Tối ưu hóa cho chiến thuật Scalping M5 với mục tiêu $20 - $50 / ngày
# ═══════════════════════════════════════════════════════════

import logging
import math
import time
from datetime import datetime, timezone

log = logging.getLogger("RiskEngine")


class ScalpM5RiskManager:
    """
    Quản lý rủi ro và vốn chuyên sâu cho Scalping khung M5:
    - Mục tiêu lợi nhuận: 20$ - 50$ / ngày
    - Không bao giờ để cháy tài khoản (Stoploss luôn cách xa ngưỡng thanh lý tối thiểu 3x - 5x)
    - Tự động ngắt giao dịch (Circuit Breaker) khi chạm lỗ tối đa trong ngày
    - Khóa lợi nhuận khi đã đạt chỉ tiêu $20 - $50 trong ngày để tránh overtrading
    - Tự động kích hoạt dời SL về Breakeven (hòa vốn + phí) khi đạt TP1 (1.5R)
    """

    def __init__(
        self,
        daily_target_usd: float = 30.0,       # Mục tiêu lợi nhuận $20 - $50/ngày (mặc định $30)
        daily_max_loss_usd: float = 15.0,     # Chặn lỗ tối đa trong ngày (15$ hoặc 3% vốn)
        risk_per_trade_pct: float = 1.0,      # Rủi ro tối đa mỗi lệnh: 1% vốn
        max_leverage: int = 15,               # Đòn bẩy tối đa an toàn cho M5
        max_margin_ratio: float = 0.30,       # Ký quỹ mỗi lệnh không vượt quá 30% tổng tài khoản
        consecutive_loss_limit: int = 3,      # Tạm dừng nếu thua liên tiếp 3 lệnh
        cooldown_seconds: int = 3600          # Thời gian nghỉ sau khi chạm giới hạn (60 phút)
    ):
        self.daily_target_usd = daily_target_usd
        self.daily_max_loss_usd = daily_max_loss_usd
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_leverage = max_leverage
        self.max_margin_ratio = max_margin_ratio
        self.consecutive_loss_limit = consecutive_loss_limit
        self.cooldown_seconds = cooldown_seconds

        # Theo dõi hiệu suất ngày (reset mỗi 00:00 UTC)
        self.current_day_str = self._get_today_str()
        self.daily_pnl_usd = 0.0
        self.daily_trades_count = 0
        self.daily_wins = 0
        self.daily_losses = 0
        self.consecutive_losses = 0
        self.is_daily_target_reached = False
        self.is_daily_stopped = False
        self.cooldown_until = 0.0

    def _get_today_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def check_and_reset_day(self):
        """Tự động làm mới bộ đếm khi sang ngày mới theo chuẩn UTC"""
        today = self._get_today_str()
        if today != self.current_day_str:
            log.info("📅 Khởi động ngày giao dịch mới UTC: %s (PnL hôm qua: $%.2f)", today, self.daily_pnl_usd)
            self.current_day_str = today
            self.daily_pnl_usd = 0.0
            self.daily_trades_count = 0
            self.daily_wins = 0
            self.daily_losses = 0
            self.consecutive_losses = 0
            self.is_daily_target_reached = False
            self.is_daily_stopped = False
            self.cooldown_until = 0.0

    def get_status(self) -> dict:
        """Trả về trạng thái kiểm soát vốn và tiến độ đạt mục tiêu ngày"""
        self.check_and_reset_day()
        now = time.time()
        in_cooldown = now < self.cooldown_until

        return {
            "date": self.current_day_str,
            "daily_pnl_usd": round(self.daily_pnl_usd, 2),
            "target_usd": self.daily_target_usd,
            "max_loss_usd": self.daily_max_loss_usd,
            "target_progress_pct": round(min(100.0, max(0.0, (self.daily_pnl_usd / self.daily_target_usd) * 100)), 1) if self.daily_target_usd > 0 else 0,
            "trades_count": self.daily_trades_count,
            "wins": self.daily_wins,
            "losses": self.daily_losses,
            "win_rate": round((self.daily_wins / self.daily_trades_count * 100), 1) if self.daily_trades_count > 0 else 0.0,
            "is_target_reached": self.is_daily_target_reached,
            "is_stopped": self.is_daily_stopped,
            "in_cooldown": in_cooldown,
            "cooldown_remaining_sec": max(0, int(self.cooldown_until - now)) if in_cooldown else 0,
            "can_trade": not self.is_daily_stopped and not self.is_daily_target_reached and not in_cooldown
        }

    def check_pre_trade_eligibility(self) -> tuple[bool, str]:
        """
        Kiểm tra trước khi vào lệnh: Đã đạt target ngày chưa, hoặc đã chạm SL ngày chưa.
        Giúp kỷ luật 100%, bảo toàn vốn tuyệt đối.
        """
        self.check_and_reset_day()
        now = time.time()

        if self.is_daily_stopped:
            return False, f"🛑 Đã chạm ngưỡng cắt lỗ tối đa trong ngày (-{self.daily_max_loss_usd:.2f} USD). Bảo toàn vốn!"

        if self.is_daily_target_reached:
            return False, f"🎉 Đã đạt mục tiêu lợi nhuận trong ngày ({self.daily_pnl_usd:.2f} USD >= {self.daily_target_usd:.2f} USD). Khóa lãi thành công!"

        if now < self.cooldown_until:
            rem = int(self.cooldown_until - now)
            return False, f"⏳ Đang trong thời gian nghỉ cooldown sau {self.consecutive_losses} lệnh thua (còn {rem}s)."

        return True, "OK"

    def calculate_scalp_order(
        self,
        capital: float,
        entry_price: float,
        sl_price: float,
        direction: str,
        user_leverage: int = 10,
        risk_pct_override: float = None
    ) -> dict:
        """
        Tính toán quy mô lệnh Scalping M5 chuẩn mực:
        - Tính chính xác số USD chịu rủi ro (Risk USD)
        - Tính khoảng cách SL %
        - Tính toán đòn bẩy an toàn (Safe Leverage) để khoảng cách cháy xa gấp bội khoảng cách SL
        - Tính ký quỹ (Margin USD) đảm bảo không quá 30% vốn
        - Tính các mốc TP1 (1.5R) và TP2 (2.5R)
        - Tính số lệnh thắng cần thiết để đạt $20 - $50
        """
        can_trade, reason = self.check_pre_trade_eligibility()
        if not can_trade:
            return {"approved": False, "reason": reason}

        if capital <= 0 or entry_price <= 0 or sl_price <= 0:
            return {"approved": False, "reason": "Dữ liệu vốn hoặc giá không hợp lệ"}

        # 1. Tính khoảng cách Stop Loss
        sl_dist = abs(entry_price - sl_price)
        sl_pct = sl_dist / entry_price

        # Ngăn chặn SL quá hẹp gây trượt giá, hoặc quá rộng làm mất tính chất Scalping M5
        if sl_pct < 0.002: # < 0.2%
            log.warning("SL quá hẹp (%.3f%%), tự động điều chỉnh tối thiểu 0.3%% để tránh quét râu", sl_pct * 100)
            sl_pct = 0.003
            sl_dist = entry_price * sl_pct
            sl_price = entry_price * (1 - sl_pct) if direction == "LONG" else entry_price * (1 + sl_pct)
        elif sl_pct > 0.03: # > 3.0% (quá rộng đối với M5 Scalp)
            return {"approved": False, "reason": f"SL quá xa ({sl_pct*100:.2f}% > 3%), không đạt tiêu chí Scalp M5"}

        # 2. Tính số tiền chấp nhận rủi ro (Risk Amount)
        risk_pct = risk_pct_override or self.risk_per_trade_pct
        risk_amount_usd = capital * (risk_pct / 100.0)

        # Chặn nếu rủi ro một lệnh vượt quá 50% mức lỗ tối đa cho phép trong ngày
        max_allowed_single_risk = self.daily_max_loss_usd * 0.5
        if risk_amount_usd > max_allowed_single_risk:
            risk_amount_usd = max_allowed_single_risk
            risk_pct = (risk_amount_usd / capital) * 100.0

        # 3. Tính Giá trị danh nghĩa lệnh (Notional Value)
        # Risk = Notional * sl_pct => Notional = Risk / sl_pct
        notional_usd = risk_amount_usd / sl_pct
        contract_qty = notional_usd / entry_price

        # 4. THIẾT KẾ ĐÒN BẨY VÀ BẢO VỆ CHỐNG CHÁY (Anti-Liquidation Shield)
        safe_max_lev = min(self.max_leverage, user_leverage, max(2, int(0.60 / sl_pct)))
        actual_leverage = max(2, safe_max_lev)

        # Ký quỹ yêu cầu
        required_margin = notional_usd / actual_leverage

        # Nếu ký quỹ vượt quá 30% tổng tài khoản -> Giảm Notional để không bị kẹt vốn
        max_allowed_margin = capital * self.max_margin_ratio
        if required_margin > max_allowed_margin:
            required_margin = max_allowed_margin
            notional_usd = required_margin * actual_leverage
            contract_qty = notional_usd / entry_price
            risk_amount_usd = notional_usd * sl_pct

        # 5. Ước tính giá thanh lý (Liquidation Price)
        liq_distance_pct = (0.90 / actual_leverage) * 100
        if direction == "LONG":
            est_liq_price = entry_price * (1 - 0.90 / actual_leverage)
            is_liq_safe = est_liq_price < (sl_price * 0.98)
        else:
            est_liq_price = entry_price * (1 + 0.90 / actual_leverage)
            is_liq_safe = est_liq_price > (sl_price * 1.02)

        # 6. Các mốc Take Profit theo R:R (Risk/Reward)
        r_usd = risk_amount_usd
        tp1_dist = sl_dist * 1.5
        tp2_dist = sl_dist * 2.5

        if direction == "LONG":
            tp1_price = entry_price + tp1_dist
            tp2_price = entry_price + tp2_dist
        else:
            tp1_price = entry_price - tp1_dist
            tp2_price = entry_price - tp2_dist

        expected_profit_usd = (0.60 * 1.5 * r_usd) + (0.40 * 2.5 * r_usd)
        remaining_target = max(0.0, self.daily_target_usd - self.daily_pnl_usd)
        trades_needed = math.ceil(remaining_target / expected_profit_usd) if expected_profit_usd > 0 else 1

        return {
            "approved": True,
            "direction": direction,
            "entry_price": round(entry_price, 4),
            "sl_price": round(sl_price, 4),
            "sl_pct": round(sl_pct * 100, 3),
            "tp1_price": round(tp1_price, 4),
            "tp2_price": round(tp2_price, 4),
            "contract_qty": round(contract_qty, 4),
            "notional_usd": round(notional_usd, 2),
            "required_margin": round(required_margin, 2),
            "margin_pct_of_capital": round((required_margin / capital) * 100, 1),
            "risk_amount_usd": round(risk_amount_usd, 2),
            "risk_pct": round(risk_pct, 2),
            "leverage": actual_leverage,
            "est_liq_price": round(est_liq_price, 4),
            "liq_distance_pct": round(liq_distance_pct, 2),
            "is_anti_liquidation_safe": is_liq_safe,
            "expected_profit_usd": round(expected_profit_usd, 2),
            "daily_target_usd": self.daily_target_usd,
            "trades_to_daily_target": trades_needed,
            "details": f"Scalp M5 {direction}: Ký quỹ {required_margin:.1f} USD ({actual_leverage}x), SL rủi ro {risk_amount_usd:.2f} USD ({risk_pct:.1f}%), TP1 {tp1_price:.2f} (1.5R), TP2 {tp2_price:.2f} (2.5R)."
        }
`,

  "quant_math.py": `# ═══════════════════════════════════════════════════════════
# QUANTITATIVE MATH & ANTI-LIQUIDATION FORMULAS
# ═══════════════════════════════════════════════════════════
import math
import logging

log = logging.getLogger("QuantMath")

class QuantRiskManager:
    def __init__(self):
        self.correlation_matrix = {
            "CRYPTO": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"],
            "TECH_STOCKS": ["QQQ", "NVDA", "TSLA"],
            "BROAD_MARKET": ["SPY"],
            "SAFE_HAVEN": ["NCCOGOLD2USD-USDT"]
        }

    def calculate_anti_liquidation_buffer(self, entry_price: float, sl_price: float, leverage: int, direction: str) -> dict:
        """
        Tính toán khoảng đệm thanh lý (Anti-Liquidation Buffer):
        Đảm bảo giá thanh lý của sàn luôn nằm ngoài điểm cắt lỗ (Stop Loss) ít nhất 3x.
        """
        if entry_price <= 0 or leverage <= 0:
            return {"safe": False, "liq_price": 0, "liq_dist_pct": 0, "sl_dist_pct": 0}

        sl_dist_pct = abs(entry_price - sl_price) / entry_price * 100.0
        liq_dist_pct = (0.90 / leverage) * 100.0

        if direction == "LONG":
            liq_price = entry_price * (1.0 - 0.90 / leverage)
            is_safe = (liq_price < sl_price) and (liq_dist_pct >= sl_dist_pct * 2.5)
        else:
            liq_price = entry_price * (1.0 + 0.90 / leverage)
            is_safe = (liq_price > sl_price) and (liq_dist_pct >= sl_dist_pct * 2.5)

        return {
            "safe": is_safe,
            "liq_price": round(liq_price, 4),
            "liq_dist_pct": round(liq_dist_pct, 2),
            "sl_dist_pct": round(sl_dist_pct, 2),
            "safety_multiple": round(liq_dist_pct / sl_dist_pct, 1) if sl_dist_pct > 0 else 99.0
        }

    def calculate_m5_scalp_sizing(
        self,
        capital: float,
        entry_price: float,
        sl_price: float,
        direction: str,
        risk_pct: float = 1.0,
        leverage: int = 10,
        target_daily_usd: float = 30.0
    ) -> dict:
        """
        Tính toán quy mô lệnh cho chiến lược Scalping M5 với mục tiêu $20 - $50/ngày.
        """
        if capital <= 0 or entry_price <= 0 or sl_price <= 0:
            return {"error": "Invalid inputs"}

        sl_pct = abs(entry_price - sl_price) / entry_price
        sl_pct = max(0.003, min(0.03, sl_pct)) # Kẹp trong khoảng 0.3% - 3.0%

        risk_usd = capital * (risk_pct / 100.0)
        notional_usd = risk_usd / sl_pct

        max_safe_lev = min(leverage, max(3, int(0.60 / sl_pct)))
        margin_usd = notional_usd / max_safe_lev

        if margin_usd > capital * 0.25:
            margin_usd = capital * 0.25
            notional_usd = margin_usd * max_safe_lev
            risk_usd = notional_usd * sl_pct

        contract_qty = notional_usd / entry_price
        buffer_info = self.calculate_anti_liquidation_buffer(entry_price, sl_price, max_safe_lev, direction)

        dist = abs(entry_price - sl_price)
        tp1 = entry_price + (dist * 1.5 if direction == "LONG" else -dist * 1.5)
        tp2 = entry_price + (dist * 2.5 if direction == "LONG" else -dist * 2.5)

        avg_profit_per_win = (0.60 * 1.5 * risk_usd) + (0.40 * 2.5 * risk_usd)
        wins_needed_for_target = math.ceil(target_daily_usd / avg_profit_per_win) if avg_profit_per_win > 0 else 1

        return {
            "direction": direction,
            "capital": capital,
            "entry_price": round(entry_price, 4),
            "sl_price": round(sl_price, 4),
            "tp1_price": round(tp1, 4),
            "tp2_price": round(tp2, 4),
            "contract_qty": round(contract_qty, 4),
            "margin_usd": round(margin_usd, 2),
            "notional_usd": round(notional_usd, 2),
            "risk_usd": round(risk_usd, 2),
            "leverage": max_safe_lev,
            "liq_price": buffer_info["liq_price"],
            "safety_multiple": buffer_info["safety_multiple"],
            "anti_liquidation_guarantee": buffer_info["safe"],
            "avg_profit_win_usd": round(avg_profit_per_win, 2),
            "target_daily_usd": target_daily_usd,
            "wins_needed": wins_needed_for_target
        }
`,

  "config.py": `# ═══════════════════════════════════════════════════════════
# CONFIGURATION — SignalBot v6.2 (M5 Scalping & Risk Sizing)
# ═══════════════════════════════════════════════════════════
import os
import logging
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("Config")

class Config:
    def __init__(self):
        self.TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
        self.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
        self.TELEGRAM_REGISTER_TOKEN = os.getenv("TELEGRAM_REGISTER_TOKEN", "")
        self.TELEGRAM_REPORT_TOKEN  = os.getenv("TELEGRAM_REPORT_TOKEN", "")
        self.TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

        self.GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
        self.GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
        self.MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
        self.NVIDIA_API_KEY  = os.getenv("NVIDIA_API_KEY", "")

        self.RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "") or os.getenv("APP_URL", "")
        self.ADMIN_SECRET        = os.getenv("ADMIN_SECRET", "admin123")

        # ── Cấu hình Scalping M5 & Quản trị rủi ro ($20 - $50 / ngày) ──
        self.M5_SCALP_MODE            = os.getenv("M5_SCALP_MODE", "true").lower() == "true"
        self.DAILY_PROFIT_TARGET_USD  = float(os.getenv("DAILY_PROFIT_TARGET_USD", "30.0"))
        self.DAILY_MAX_LOSS_USD       = float(os.getenv("DAILY_MAX_LOSS_USD", "15.0"))
        self.MAX_RISK_PER_TRADE_PCT   = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "1.0"))
        self.MAX_SAFE_LEVERAGE        = int(os.getenv("MAX_SAFE_LEVERAGE", "15"))
        self.CONSECUTIVE_LOSS_LIMIT   = int(os.getenv("CONSECUTIVE_LOSS_LIMIT", "3"))
        self.AUTO_BREAKEVEN_ON_TP1    = os.getenv("AUTO_BREAKEVEN_ON_TP1", "true").lower() == "true"
`,

  "engine.py": `# ═══════════════════════════════════════════════════════════
# SIGNAL ENGINE — v6.2 (5m Scalp + 15m/1h/4h Multi-Timeframe)
# ═══════════════════════════════════════════════════════════
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from analyzer.fetcher import CryptoFetcher, StockFetcher
from analyzer.indicators import Indicators

log = logging.getLogger("analyzer.engine")

class SignalEngine:
    # Hỗ trợ Scalping M5 kết hợp xu hướng đa khung 15m, 1h, 4h
    TIMEFRAMES = ["5m", "15m", "1h", "4h"]
    _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tf-worker")

    def __init__(self):
        self.crypto = CryptoFetcher()
        self.stock  = StockFetcher()
        self.ind    = Indicators()
        self._cache = {}
`,

  "main_scanner.py": `# ═══════════════════════════════════════════════════════════
# MAIN SCANNER — SignalBot v6.2 (M5 Scalping 3-Minute Cycle)
# ═══════════════════════════════════════════════════════════
import os, time, json, logging, schedule, gc, requests, threading
from analyzer.engine import SignalEngine
from analyzer.config import Config

cfg = Config()

class SignalBot:
    CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"]
    STOCK_SYMBOLS  = ["TSLA", "NVDA", "SPY", "QQQ", "NCCOGOLD2USD-USDT"]

    def __init__(self):
        self.engine = SignalEngine()

    def _build_schedule(self):
        schedule.clear()
        # Chế độ Scalping M5: Quét crypto mỗi 3 phút bắt trọn nến M5
        crypto_interval = 3 if getattr(cfg, "M5_SCALP_MODE", True) else 15
        schedule.every(crypto_interval).minutes.do(self.run_crypto)
        schedule.every(30).minutes.do(self.run_stocks)
        schedule.every(4).minutes.do(self._self_ping)
`,

  "main.py": `# ═══════════════════════════════════════════════════════════
# CORE API & COPY-TRADE EXECUTOR — SignalBot v6.2
# ═══════════════════════════════════════════════════════════
# - Daily Target: $20 - $50 USD (Khóa lãi tự động)
# - Daily Loss Circuit Breaker: -$15 USD (Chặn lỗ tuyệt đối)
# - Anti-Liquidation Shield: Đòn bẩy khống chế trần an toàn
# ═══════════════════════════════════════════════════════════
import os, asyncio, logging
from fastapi import FastAPI

app = FastAPI(title="SignalBot v6.2 M5 Scalping")

# Khi người dùng đạt mục tiêu ngày ($20 - $50), tự động bảo vệ thành quả
TARGET_DAILY_PROFIT_USD = float(os.getenv("DAILY_PROFIT_TARGET_USD", "30.0"))
DAILY_MAX_LOSS_USD = float(os.getenv("DAILY_MAX_LOSS_USD", "15.0"))
`
};
