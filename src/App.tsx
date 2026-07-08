import React, { useState, useEffect, useRef } from "react";
import { 
  Play, ShieldAlert, Cpu, Database, Settings, Terminal, Copy, Check, RefreshCw, 
  TrendingUp, TrendingDown, BookOpen, Layers, Users, Zap, Bell, CheckCircle2, ChevronRight, FileCode 
} from "lucide-react";

// Mock data generator for simulating live orderbook and liquidity sweeps
const SYMBOLS = [
  { name: "BTCUSDT", price: 92850.5, leverage: "20x", step: 0.5, decimal: 1, type: "CRYPTO" },
  { name: "ETHUSDT", price: 3452.2, leverage: "20x", step: 0.1, decimal: 2, type: "CRYPTO" },
  { name: "BNBUSDT", price: 612.4, leverage: "15x", step: 0.05, decimal: 2, type: "CRYPTO" },
  { name: "HYPEUSDT", price: 18.25, leverage: "10x", step: 0.01, decimal: 3, type: "CRYPTO" },
  { name: "XAUUSD", price: 2364.8, leverage: "100x", step: 0.1, decimal: 2, type: "GOLD" },
  { name: "TSLA", price: 184.5, leverage: "5x", step: 0.05, decimal: 2, type: "STOCK" },
  { name: "NVDA", price: 124.8, leverage: "5x", step: 0.05, decimal: 2, type: "STOCK" }
];

export default function App() {
  const [selectedSym, setSelectedSym] = useState(SYMBOLS[0]);
  const [price, setPrice] = useState(SYMBOLS[0].price);
  
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
  const [users, setUsers] = useState([
    { telegram_id: "6286755886", tier: "TIER1", capital: 350.0, leverage: 5, auto: true, risk: 2.0, conf: 68 },
    { telegram_id: "8827361923", tier: "TIER2", capital: 1250.0, leverage: 5, auto: true, risk: 1.5, conf: 75 },
    { telegram_id: "9018273612", tier: "TIER3", capital: 5500.0, leverage: 3, auto: true, risk: 1.0, conf: 80 }
  ]);
  const [logs, setLogs] = useState<string[]>([]);
  
  // AI Pipeline State
  const [isAnalyzing, setIsAIAnalyzing] = useState(false);
  const [analysisStage, setAnalysisStage] = useState(0);
  const [analysisOutput, setAnalysisOutput] = useState<any>(null);
  const [activeAgentTab, setActiveAgentTab] = useState("TECHNICAL");
  
  // Code Viewer State
  const [activeCodeFile, setActiveCodeFile] = useState("engine.py");
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
    addLog(`🔄 Hệ thống đã khởi động quét chu kỳ mới cho ${selectedSym.name}`);
    generateOrderBook(selectedSym.price);
    generateLiquidationLevels(selectedSym.price);
  }, [selectedSym]);

  const symbol = selectedSym.name;

  // Helper to get active symbol details
  function getSymbolConfig() {
    return SYM_MAP[symbol] || symbol;
  }

  // Real-time tick simulator
  useEffect(() => {
    const interval = setInterval(() => {
      const currentPrice = getLatestPriceForSymbol(symbol);
      const randChange = (Math.random() - 0.5) * selectedSym.step * 1.5;
      const newPrice = Math.max(0.1, currentPrice + randChange);
      updatePriceAndL2(newPrice);
    }, 1500);
    return () => clearInterval(interval);
  }, [symbol, selectedSym]);

  // Orderbook generation logic matching fetcher.py
  function generateOrderBook(basePrice: number) {
    const bidsArr = [];
    const asksArr = [];
    const step = selectedSym.step;
    const decimal = selectedSym.type === "CRYPTO" ? 1 : 2;

    let totalBidsUsd = 0;
    let totalAsksUsd = 0;
    
    // Generate 15 asks above basePrice
    let askWallIdx = Math.floor(Math.random() * 8) + 4; // Large order wall
    for (let k = 1; k <= 15; k++) {
      const p = basePrice + k * step;
      let size = Math.random() * 1.5 + 0.1;
      if (k === askWallIdx) size *= 8.5; // Huge sell wall
      const usd = p * size;
      totalAsksUsd += usd;
      asksArr.push({ price: p, qty: size, usd });
    }
    
    // Generate 15 bids below basePrice
    let bidWallIdx = Math.floor(Math.random() * 8) + 4;
    for (let k = 1; k <= 15; k++) {
      const p = basePrice - k * step;
      let size = Math.random() * 1.5 + 0.1;
      if (k === bidWallIdx) size *= 9.2; // Huge buy wall
      const usd = p * size;
      totalBidsUsd += usd;
      bidsArr.push({ price: p, qty: size, usd });
    }

    const sortedAsks = asksArr.sort((a, b) => b.price - a.price); // Asks high to low
    const sortedBids = bidsArr.sort((a, b) => b.price - a.price); // Bids high to low

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

  // Liquidation calculation logic matching fetcher.py
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

    // Cascade Risk definition: if current price is within 1.5% of 50x / 100x liquidations
    const closeLiqLong = longs.some(l => l.leverage >= 50 && l.distance < 1.5);
    const closeLiqShort = shorts.some(s => s.leverage >= 50 && s.distance < 1.5);
    setCascadeRisk(closeLiqLong || closeLiqShort);
    setDominantSide(Math.random() > 0.5 ? "LONG" : "SHORT");
  }

  function updatePriceAndL2(newPrice: number) {
    setPrice(newPrice);
    
    // Check for Liquidity sweeps relative to swing low/high
    if (newPrice < lastSwingLow) {
      // Swept low, check if retraces
      setSweepTriggered("BULLISH_SWEEP");
      addLog(`🔥 BẪY THANH KHOẢN (BULLISH SWEEP): Giá quét râu dưới swing low $${lastSwingLow.toFixed(1)}! Lực mua cản hấp thụ.`);
      setLastSwingLow(newPrice * 0.992); // Set new lower support
      setTimeout(() => setSweepTriggered(null), 8000);
    } else if (newPrice > lastSwingHigh) {
      setSweepTriggered("BEARISH_SWEEP");
      addLog(`🛑 BẪY THANH KHOẢN (BEARISH SWEEP): Giá quét râu trên swing high $${lastSwingHigh.toFixed(1)}! Lực xả cản dội xuống.`);
      setLastSwingHigh(newPrice * 1.008); // Set new higher resistance
      setTimeout(() => setSweepTriggered(null), 8000);
    }

    // Regene book near new price
    generateOrderBook(newPrice);
    generateLiquidationLevels(newPrice);
  }

  function addLog(text: string) {
    const timestamp = new Date().toLocaleTimeString("vi-VN");
    setLogs(prev => [`[${timestamp}] ${text}`, ...prev.slice(0, 19)]);
  }

  // Simulate AI Pipeline Decision
  const handleRunAIEngine = () => {
    if (isAnalyzing) return;
    setIsAIAnalyzing(true);
    setAnalysisStage(1);
    setAnalysisOutput(null);
    addLog(`🤖 Khởi động quy trình thẩm định 12 AI Agents Stage 1 cho ${selectedSym.name}...`);

    // Stage 1 Duration
    setTimeout(() => {
      setAnalysisStage(2);
      addLog("🤖 Stage 1 hoàn thành. 4 Domain Analysts đã bỏ phiếu phân loại kĩ thuật, CVD và vĩ mô.");
      
      // Stage 2 Duration
      setTimeout(() => {
        setAnalysisStage(3);
        addLog("🤖 Stage 2 hoàn thành. Bull vs Bear Researchers đã hoàn tất tranh luận sâu.");

        // Stage 3-4 Duration
        setTimeout(() => {
          setAnalysisStage(4);
          addLog("🤖 Stage 3 & 4 hoàn thành. Trader Agent dựng kế hoạch, Aggressive và Conservative Risk Analysts đã bỏ phiếu.");

          // Stage 5 Duration
          setTimeout(() => {
            setAnalysisStage(5);
            addLog("🏁 Stage 5 hoàn thành! CIO đã phê duyệt quyết định tối hậu kèm bộ lọc Statistical Win Rate.");
            
            // Build the final CIO message payload
            const finalDirection = imbalance > 15 ? "LONG" : imbalance < -15 ? "SHORT" : "WAIT";
            const rawConfidence = Math.round(72 + Math.random() * 18);
            const statisticalConfidence = Math.round(rawConfidence * (finalDirection === "LONG" ? 1.04 : 0.95));

            const atr = selectedSym.price * 0.012;
            const entry = selectedSym.price;
            const sl = finalDirection === "LONG" ? entry - atr : entry + atr;
            const tp1 = finalDirection === "LONG" ? entry + atr * 2.0 : entry - atr * 2.0;
            const tp2 = finalDirection === "LONG" ? entry + atr * 3.5 : entry - atr * 3.5;

            const mockOutput = {
              symbol: selectedSym.name,
              direction: finalDirection,
              confidence: rawConfidence,
              stat_confidence: statisticalConfidence,
              plan: { entry, sl, tp1, tp2 },
              oi_delta: (Math.random() * 4).toFixed(2),
              funding: (0.01 + Math.random() * 0.03).toFixed(4),
              is_sweep: sweepTriggered !== null,
              sweep_p: price,
              agents: {
                technical: `Chỉ báo RSI đang nằm ở mức ${Math.round(40 + Math.random() * 20)} điểm. Đường EMA 20 nằm ${finalDirection === "LONG" ? "trên" : "dưới"} EMA 50, cấu trúc khung 1H tương đối vững vàng.`,
                onchain: `CVD đang ghi nhận xu hướng ${imbalance > 0 ? "BULLISH (Gom hàng)" : "BEARISH (Phân phối)"} với lực mua chiếm ${50 + Math.round(imbalance / 2)}%. Không phát hiện khối lệnh Whale rút bất thường.`,
                macro: `Sau báo cáo CPI mới nhất, lo ngại rủi ro lạm phát giảm bớt, DXY giảm nhẹ hỗ trợ cho đợt hồi phục này.`,
                momentum: `VWAP đang đóng vai trò làm hỗ trợ cứng tại mức $${(entry * 0.997).toFixed(1)}. Lực breakout ${finalDirection === "LONG" ? "đà tăng" : "đà giảm"} đang được kích hoạt tích cực.`
              }
            };
            
            setAnalysisOutput(mockOutput);
            setIsAIAnalyzing(false);

            // If auto trade is active, simulate pushing signal to database & copy trading
            if (autoTrade && finalDirection !== "WAIT") {
              addLog(`🚨 COP_TRADE: Gửi tín hiệu ${finalDirection} ${selectedSym.name} sang Redis. Phân phối lệnh sao chép đến 3 Users...`);
            }
          }, 1500);
        }, 1500);
      }, 1500);
    }, 1500);
  };

  const handleCopyCode = () => {
    navigator.clipboard.writeText(pythonFiles[activeCodeFile]);
    setCopiedText(true);
    setTimeout(() => setCopiedText(false), 2000);
  };

  // Helper selectors
  function selectedSymbol() {
    return selectedSym.name;
  }
  function selectedPrice() {
    return price;
  }
  function selectedSymbolPrice() {
    return selectedSym.price;
  }

  return (
    <div className="min-h-screen bg-[#030611] text-[#e2e8f0] flex flex-col antialiased selection:bg-emerald-500 selection:text-black">
      
      {/* Dynamic Header */}
      <header className="border-b border-[#1b263e] bg-[#080d1a] px-6 py-4 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 shrink-0">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-extrabold tracking-wider text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 via-mint-300 to-cyan-400">
              SIGNAL<span className="text-[#05f38c]">BOT</span> v6.2
            </h1>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1.5 animate-pulse">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block"></span>
              CORE ONLINE
            </span>
          </div>
          <p className="text-xs text-[#718096] mt-1 font-medium">
            Hệ thống 12 AI Agents quyết định lệnh copy-trade tự động thời gian thực • BingX Futures Integration
          </p>
        </div>

        <div className="flex items-center gap-3 self-stretch md:self-auto justify-between md:justify-end">
          <div className="text-right font-mono text-xs">
            <span className="text-[#718096]">UTC LOCAL: </span>
            <span className="text-emerald-400 font-bold">{timeStr || "Đang lấy..."}</span>
          </div>
          <div className="h-6 w-px bg-[#1b263e]"></div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-[#718096]">AUTO STATE:</span>
            <span className={`badge text-[11px] font-bold px-2.5 py-1 rounded border ${autoTrade ? "bg-emerald-950/40 text-emerald-400 border-emerald-500/20" : "bg-red-950/40 text-red-400 border-red-500/20"}`}>
              {autoTrade ? "ACTIVE" : "STANDBY"}
            </span>
          </div>
        </div>
      </header>

      {/* Main Workspace Layout */}
      <main className="flex-1 p-6 max-w-[1700px] w-full mx-auto grid grid-cols-1 xl:grid-cols-12 gap-6 overflow-hidden">
        
        {/* LEFT COLUMN: Controls, Simulation and Stats (4 cols) */}
        <section className="xl:col-span-4 flex flex-col gap-6">
          
          {/* Symbol Select & Quick Simulation */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
            <h2 className="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
              <Zap className="w-4 h-4 text-emerald-400" /> Chọn Tài Sản Theo Dõi
            </h2>
            
            <div className="grid grid-cols-3 gap-2">
              {SYMBOLS.map(sym => (
                <button
                  key={sym.name}
                  id={`sym-btn-${sym.name}`}
                  onClick={() => setSelectedSym(sym)}
                  className={`py-2 px-3 rounded-lg text-xs font-bold transition-all flex flex-col items-center justify-between gap-1 border ${
                    selectedSym.name === sym.name 
                      ? "bg-emerald-950/30 text-[#05f38c] border-emerald-500/40 shadow-[0_0_15px_-3px_rgba(5,243,140,0.15)]"
                      : "bg-[#080d1a] text-[#718096] border-transparent hover:border-[#1b263e] hover:text-[#e2e8f0]"
                  }`}
                >
                  <span>{sym.name}</span>
                  <span className="text-[10px] text-[#718096]">{sym.type}</span>
                </button>
              ))}
            </div>

            {/* Price Tracker Display */}
            <div className="bg-[#070b16] border border-[#141d2e] rounded-lg p-4 flex justify-between items-center relative overflow-hidden">
              {sweepTriggered && (
                <div className={`absolute inset-0 opacity-[0.06] animate-pulse ${sweepTriggered === "BULLISH_SWEEP" ? "bg-emerald-500" : "bg-red-500"}`} />
              )}
              
              <div>
                <span className="text-[11px] text-[#718096] font-bold block uppercase tracking-wider">Giá Thị Trường 1H</span>
                <span className="text-2xl font-black font-mono text-[#e2e8f0] tracking-tight mt-1 inline-block">
                  ${price.toLocaleString("en-US", { minimumFractionDigits: selectedSym.decimal })}
                </span>
              </div>

              <div className="text-right">
                <span className="text-[11px] text-[#718096] font-bold block uppercase tracking-wider">Biến động (Mô Phỏng)</span>
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

          {/* Real-time Liquidity & Liquidation Levels */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
            <div className="flex justify-between items-center">
              <h2 className="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                <Layers className="w-4 h-4 text-emerald-400" /> Bản đồ thanh lý & Bẫy thanh khoản
              </h2>
              {sweepTriggered && (
                <span className={`badge text-[10px] font-black px-2 py-0.5 rounded-full animate-bounce ${
                  sweepTriggered === "BULLISH_SWEEP" ? "bg-emerald-500/25 text-[#05f38c]" : "bg-red-500/25 text-[#ff3b5c]"
                }`}>
                  🚨 SWEPT SWING ZONE
                </span>
              )}
            </div>

            {/* Sweep Trigger Overlay */}
            {sweepTriggered ? (
              <div className={`p-3 rounded-lg border flex gap-3 items-center ${
                sweepTriggered === "BULLISH_SWEEP" 
                  ? "bg-emerald-950/30 border-emerald-500/30 text-emerald-300"
                  : "bg-red-950/30 border-red-500/30 text-red-300"
              }`}>
                <Bell className="w-5 h-5 shrink-0 animate-swing" />
                <div className="text-xs">
                  <span className="font-bold block">Phát hiện Liquidity Sweep ({sweepTriggered})</span>
                  Giá chạm mức râu quét cực đại và đảo chiều nhanh chóng. Gom râu thanh khoản thành công!
                </div>
              </div>
            ) : (
              <div className="p-3 bg-[#070b16] rounded-lg border border-[#141d2e] text-xs text-[#718096] flex gap-2.5 items-center">
                <span className="w-2 h-2 rounded-full bg-[#718096] animate-ping" />
                Đang quét các ngưỡng hỗ trợ/kháng cự lịch sử...
              </div>
            )}

            {/* Liquidation Levels Tables */}
            <div className="grid grid-cols-2 gap-4">
              {/* Long Liquidations */}
              <div className="flex flex-col gap-2">
                <span className="text-[11px] font-bold text-emerald-400 uppercase tracking-wider block">Ngưỡng Long Liq</span>
                <div className="bg-[#070b16] border border-[#141d2e] rounded-lg p-2.5 flex flex-col gap-1.5 font-mono text-[11px]">
                  {liqLongs.slice(0, 4).map(liq => (
                    <div key={liq.leverage} className="flex justify-between">
                      <span className="text-[#718096]">{liq.leverage}x leverage</span>
                      <span className="text-emerald-400 font-bold">${liq.price.toLocaleString("en-US", { minimumFractionDigits: 1 })}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Short Liquidations */}
              <div className="flex flex-col gap-2">
                <span className="text-[11px] font-bold text-red-400 uppercase tracking-wider block">Ngưỡng Short Liq</span>
                <div className="bg-[#070b16] border border-[#141d2e] rounded-lg p-2.5 flex flex-col gap-1.5 font-mono text-[11px]">
                  {liqShorts.slice(0, 4).map(liq => (
                    <div key={liq.leverage} className="flex justify-between">
                      <span className="text-[#718096]">{liq.leverage}x leverage</span>
                      <span className="text-red-400 font-bold">${liq.price.toLocaleString("en-US", { minimumFractionDigits: 1 })}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Cascade risk & dominant side */}
            <div className="flex gap-3 mt-1">
              <div className="flex-1 bg-[#070b16] border border-[#141d2e] rounded-lg p-2.5 text-center">
                <span className="text-[10px] text-[#718096] uppercase font-bold block">Phe áp đảo vị thế</span>
                <span className={`text-xs font-bold block mt-1 ${dominantSide === "LONG" ? "text-emerald-400" : "text-red-400"}`}>
                  {dominantSide === "LONG" ? "🔼 BÒ (BUYERS)" : "🔽 GẤU (SELLERS)"}
                </span>
              </div>
              <div className={`flex-1 border rounded-lg p-2.5 text-center ${
                cascadeRisk 
                  ? "bg-red-950/20 border-red-500/30 text-red-400 animate-pulse"
                  : "bg-[#070b16] border-[#141d2e] text-[#718096]"
              }`}>
                <span className="text-[10px] uppercase font-bold block">Nguy cơ Cascade liq</span>
                <span className="text-xs font-bold block mt-1">
                  {cascadeRisk ? "🚨 HIGH CASCADE RISK" : "⬜ NORMAL RISK"}
                </span>
              </div>
            </div>
          </div>

          {/* Admin Web controls Panel */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
            <h2 className="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
              <Settings className="w-4 h-4 text-emerald-400" /> Bảng điều khiển Admin
            </h2>

            <div className="flex flex-col gap-2">
              {/* Toggles */}
              <div className="flex justify-between items-center p-2.5 bg-[#070b16] border border-[#141d2e] rounded-lg">
                <span className="text-xs font-bold">Auto-Trade Toàn Hệ Thống</span>
                <button 
                  onClick={() => setAutoTrade(!autoTrade)}
                  className={`w-12 h-6 rounded-full p-1 transition-all duration-300 relative ${autoTrade ? "bg-emerald-500" : "bg-[#1f2937]"}`}
                >
                  <div className={`w-4 h-4 rounded-full bg-black transition-all ${autoTrade ? "ml-6" : "ml-0"}`} />
                </button>
              </div>

              <div className="flex justify-between items-center p-2.5 bg-[#070b16] border border-[#141d2e] rounded-lg">
                <span className="text-xs font-bold text-red-400">Kill Switch (Đóng All Vị Thế)</span>
                <button 
                  onClick={() => {
                    setKillSwitch(!killSwitch);
                    if (!killSwitch) {
                      addLog("🔥 EMERGENCY: Đã bật KILL SWITCH! Gửi yêu cầu đóng toàn bộ các vị thế Users ngay lập tức.");
                    }
                  }}
                  className={`w-12 h-6 rounded-full p-1 transition-all duration-300 relative ${killSwitch ? "bg-red-600" : "bg-[#1f2937]"}`}
                >
                  <div className={`w-4 h-4 rounded-full bg-black transition-all ${killSwitch ? "ml-6" : "ml-0"}`} />
                </button>
              </div>
            </div>

            {/* Users Tier list view */}
            <div className="flex flex-col gap-2 mt-2">
              <span className="text-[11px] font-bold text-[#718096] uppercase tracking-wider block">Quản lý Tài Khoản Users copy</span>
              {users.map(u => (
                <div key={u.telegram_id} className="flex justify-between items-center bg-[#070b16] border border-[#141d2e] rounded-lg p-2.5 text-xs">
                  <div>
                    <span className="font-bold block">UID: {u.telegram_id}</span>
                    <span className="text-[10px] text-[#718096]">Vốn: ${u.capital} • Đòn bẩy: {u.leverage}x</span>
                  </div>
                  <span className={`px-2 py-0.5 rounded text-[10px] font-black ${
                    u.tier === "TIER1" ? "bg-blue-950/40 text-blue-400 border border-blue-500/20" :
                    u.tier === "TIER2" ? "bg-amber-950/40 text-amber-400 border border-amber-500/20" :
                    "bg-emerald-950/40 text-emerald-400 border border-emerald-500/20"
                  }`}>
                    {u.tier === "TIER1" ? "🐟 CÁ CON" : u.tier === "TIER2" ? "🐠 TIÊU CHUẨN" : "🦈 CÁ MẬP"}
                  </span>
                </div>
              ))}
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
                    {/* Depth Fill Bar on background */}
                    <div 
                      className="absolute right-0 top-0 bottom-0 bg-red-500/5 transition-all"
                      style={{ width: `${Math.min(100, (ask.usd / sellWall.usd) * 100)}%` }}
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
                      style={{ width: `${Math.min(100, (bid.usd / buyWall.usd) * 100)}%` }}
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
                <Cpu className="w-4 h-4 text-emerald-400" /> Mô phỏng 12 AI Agent Pipeline (v6.1)
              </h2>
              <button 
                onClick={handleRunAIEngine}
                disabled={isAnalyzing}
                className="py-1 px-3 rounded-lg text-xs font-black bg-gradient-to-r from-emerald-500 to-cyan-500 text-black hover:opacity-90 disabled:opacity-50 transition-all flex items-center gap-1.5 shadow-[0_0_15px_rgba(16,185,129,0.15)]"
              >
                <Play className="w-3.5 h-3.5 fill-black" /> CHẠY PHÂN TÍCH L2 + AI
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
                    {analysisStage === 1 && "Stage 1: 4 Domain Analysts (Technical, On-chain, Macro, Momentum) đang biểu quyết..."}
                    {analysisStage === 2 && "Stage 2: Bull Researcher vs Bear Researcher tranh biện số liệu..."}
                    {analysisStage === 3 && "Stage 3 & 4: Trader dựng kế hoạch và 3 Risk Officers rà soát độ khớp..."}
                    {analysisStage === 4 && "Stage 5: Tổng hợp biểu quyết, CIO thẩm định và cấp sắc lệnh..."}
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
                  {["TECHNICAL", "ONCHAIN", "MACRO", "MOMENTUM"].map(tab => (
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
                  {activeAgentTab === "MOMENTUM" && `[MOMENTUM_ANALYST]: ${analysisOutput.agents.momentum}`}
                </div>

                {/* Final Verdict Telegram Signal representation */}
                <div className="border border-emerald-500/20 bg-emerald-950/10 rounded-xl p-4 flex flex-col gap-3">
                  <div className="flex justify-between items-start">
                    <div>
                      <span className="px-2 py-0.5 rounded text-[10px] font-black bg-emerald-500/20 text-[#05f38c]">
                        🟢 TÍN HIỆU {analysisOutput.direction} — {analysisOutput.symbol}
                      </span>
                      <h3 className="text-sm font-black mt-2 font-mono">ĐỘ TIN CẬY: {analysisOutput.stat_confidence}%</h3>
                    </div>
                    <span className="text-[10px] text-[#718096] font-mono">AI Consensus: Strong (92% Bull)</span>
                  </div>

                  <div className="grid grid-cols-2 gap-3 text-xs font-mono bg-[#040811] p-3 rounded-lg border border-emerald-500/10">
                    <div>
                      <span className="text-[#718096] text-[10px] block">Vào lệnh</span>
                      <span className="text-[#e2e8f0] font-bold">${analysisOutput.plan.entry.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })}</span>
                    </div>
                    <div>
                      <span className="text-red-400 text-[10px] block">🛑 Cắt lỗ (SL)</span>
                      <span className="text-red-400 font-bold">${analysisOutput.plan.sl.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })}</span>
                    </div>
                    <div>
                      <span className="text-emerald-400 text-[10px] block">🎯 Chốt lời 1 (TP1)</span>
                      <span className="text-emerald-400 font-bold">${analysisOutput.plan.tp1.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })}</span>
                    </div>
                    <div>
                      <span className="text-blue-400 text-[10px] block">🏆 Chốt lời 2 (TP2)</span>
                      <span className="text-blue-400 font-bold">${analysisOutput.plan.tp2.toLocaleString(undefined, { minimumFractionDigits: selectedSym.decimal })}</span>
                    </div>
                  </div>

                  {/* L2 Orderbook Imbalance and sweep verification inside Signal card */}
                  <div className="text-[11px] text-[#718096] border-t border-[#1b263e]/40 pt-2 flex flex-col gap-1 font-mono">
                    <div className="flex justify-between">
                      <span>🧱 Sổ lệnh Imbalance:</span>
                      <span className="text-emerald-400 font-bold">+{imbalance}% (Cực mạnh)</span>
                    </div>
                    <div className="flex justify-between">
                      <span>🔥 Bẫy Thanh Khoản:</span>
                      <span className={sweepTriggered ? "text-[#05f38c] font-bold" : "text-[#718096]"}>
                        {sweepTriggered ? `PHÁT HIỆN (${sweepTriggered})` : "Không có"}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span>🔗 BingX Copy:</span>
                      <span className="text-[#e2e8f0]">Đã gửi webhook tự động thành công</span>
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
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 flex-1 min-h-[220px]">
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

          {/* Quick links to download the python backend files we made */}
          <div className="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
            <h2 className="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
              <FileCode className="w-4 h-4 text-emerald-400" /> Tệp Tin Python Đã Sửa Đổi
            </h2>
            <p className="text-[11px] text-[#718096]">
              Toàn bộ cấu trúc hệ thống trade bot v6.1 của bạn đã được tối ưu hóa, thêm bẫy thanh khoản và sổ lệnh hoàn chỉnh trong thư mục <code>/bot_code</code> của workspace này.
            </p>

            <div className="flex flex-col gap-1.5">
              {Object.keys(pythonFiles).map(fileKey => (
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

      {/* Code Viewer Tab (Bottom section or drawer) */}
      <section className="mx-6 mb-8 bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4">
        <div className="flex justify-between items-center">
          <div>
            <h2 className="text-sm font-bold text-[#e2e8f0] tracking-wider uppercase flex items-center gap-2">
              <FileCode className="w-4 h-4 text-emerald-400" /> Trình Duyệt Mã Nguồn: <span className="text-emerald-400 font-mono">{activeCodeFile}</span>
            </h2>
            <p className="text-xs text-[#718096] mt-0.5">Sao chép tệp tin này và lưu đè lên Render.com hoặc VPS của bạn.</p>
          </div>

          <button
            onClick={handleCopyCode}
            className="py-1.5 px-4 rounded-lg text-xs font-bold bg-[#141d2e] border border-[#1b263e] text-[#e2e8f0] hover:bg-[#1b263e] transition-all flex items-center gap-2"
          >
            {copiedText ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-400" /> ĐÃ SAO CHÉP!
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" /> SAO CHÉP MÃ NGUỒN
              </>
            )}
          </button>
        </div>

        <div className="bg-[#040811] rounded-lg border border-[#141d2e] p-4 overflow-x-auto font-mono text-xs text-[#a0aec0] max-h-[450px] scrollbar-thin leading-relaxed">
          <pre>{pythonFiles[activeCodeFile]}</pre>
        </div>
      </section>
    </div>
  );
}

// Helpers for mock price simulation
function selectedSymbol() {
  return "BTCUSDT";
}

function selectedPrice() {
  return 92850.5;
}

const SYM_MAP: Record<string, any> = {
  "BTCUSDT": { name: "BTCUSDT" }
};

const getLatestPriceForSymbol = (sym: string) => {
  const match = SYMBOLS.find(s => s.name === sym);
  return match ? match.price : 92850.5;
};

// Complete dictionary of customized Python files written in the background for display/copy
const pythonFiles: Record<string, string> = {
  "engine.py": `# ═══════════════════════════════════════════════════════════
# 5. SIGNAL ENGINE — v6.1 (Parallel TF + Smart Confidence)
# ═══════════════════════════════════════════════════════════
import time
import logging
import math
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from analyzer.fetcher import CryptoFetcher, StockFetcher
from analyzer.indicators import Indicators

log = logging.getLogger("analyzer.engine")


class SignalEngine:
    TIMEFRAMES = ["15m", "1h", "4h", "1d"]

    # [FIX] Dùng chung 1 ThreadPoolExecutor thay vì tạo mới mỗi lần full_analysis
    # max_workers=4 khớp với số TF để tất cả chạy song song
    _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tf-worker")

    def __init__(self):
        self.crypto = CryptoFetcher()
        self.stock  = StockFetcher()
        self.ind    = Indicators()
        self._cache = {}

    def _fetcher(self, symbol):
        if symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"):
            return self.crypto, "CRYPTO"
        if symbol in ("TSLA", "NVDA", "SPY", "QQQ"):
            return self.stock, "STOCK"
        if symbol == "NCCOGOLD2USD-USDT":
            return self.stock, "GOLD"
        return self.crypto, "CRYPTO"

    def is_tradeable(self, symbol):
        if symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"):
            return True, "Crypto 24/7"
        if symbol in ("TSLA", "NVDA", "SPY", "QQQ"):
            return self.stock.market_open()
        if symbol == "NCCOGOLD2USD-USDT":
            return self.stock.is_gold_open()
        return True, "OK"

    def analyze_tf(self, symbol, interval, fetcher):
        data   = fetcher.klines(symbol, interval)
        closes = data["close"]
        highs  = data["high"]
        lows   = data["low"]
        vols   = data["volume"]
        tbvols = data["taker_buy_vol"]
        price  = closes[-1]

        # ── Indicators ─────────────────────────────────────────
        rsi_v  = self.ind.rsi(closes)
        macd_d = self.ind.macd(closes)
        bb     = self.ind.bollinger(closes)
        fibo   = self.ind.fibonacci(highs, lows, closes)
        wyc    = self.ind.wyckoff(closes, highs, lows, vols)
        cvd_d  = self.ind.cvd(closes, tbvols, vols)
        bo     = self.ind.breakout_detector(closes, highs, lows, vols)
        whale  = self.ind.whale_detector(closes, vols, tbvols)
        vol_d  = self.ind.volume_analysis(closes, highs, lows, vols, tbvols)
        candle = self.ind.candlestick_patterns(data["open"], highs, lows, closes, vols)
        mstruct = self.ind.market_structure(closes, highs, lows)
        elliott = self.ind.elliott_wave_analysis(closes, highs, lows)
        fvg = self.ind.order_flow_fvg(highs, lows, closes)

        ema20  = self.ind.ema(closes, 20)
        ema50  = self.ind.ema(closes, 50)
        ema200 = self.ind.ema(closes, 200)
        ema_bull = price > ema20 > ema50 > ema200
        ema_bear = price < ema20 < ema50 < ema200

        # ── ATR ─────────────────────────────────────────────────
        if len(highs) >= 14 and len(lows) >= 14:
            trs = [max(highs[i] - lows[i],
                       abs(highs[i] - closes[i-1]),
                       abs(lows[i]  - closes[i-1]))
                   for i in range(1, min(15, len(closes)))]
            atr = sum(trs) / len(trs) if trs else price * 0.01
        else:
            atr = price * 0.01
        atr_pct = atr / price * 100

        # ── Trend strength (ADX simplified) ─────────────────────
        if len(closes) >= 14:
            changes = [abs(closes[i] - closes[i-1]) for i in range(-14, 0)]
            avg_change = sum(changes) / 14
            trend_strength = avg_change / (atr if atr > 0 else 1)
            is_trending = trend_strength > 0.5
        else:
            is_trending = True
            trend_strength = 1.0

        # ══════════════════════════════════════════════════════
        # SCORING
        # ══════════════════════════════════════════════════════
        score = 50

        # 1. RSI (weight 15)
        if is_trending:
            if rsi_v < 40:   score += 12
            elif rsi_v < 48: score += 6
            elif rsi_v > 60: score -= 6
            elif rsi_v > 72: score -= 12
        else:
            if rsi_v < 30:   score += 15
            elif rsi_v < 45: score += 8
            elif rsi_v > 70: score -= 15
            elif rsi_v > 55: score -= 8

        # 2. EMA Stack (weight 12)
        if ema_bull:   score += 12
        elif ema_bear: score -= 12
        elif price > ema20:  score += 4
        elif price < ema20:  score -= 4

        # 3. MACD (weight 8)
        hist = macd_d.get("hist", 0)
        if macd_d["cross"] == "BULL_CROSS" and hist > 0:
            score += 8
        elif macd_d["cross"] == "BEAR_CROSS" and hist < 0:
            score -= 8
        elif macd_d["cross"] == "BULL_CROSS":
            score += 4
        elif macd_d["cross"] == "BEAR_CROSS":
            score -= 4

        # 4. Bollinger Bands (weight 8)
        if bb["pct"] < 15:   score += 8
        elif bb["pct"] < 30: score += 4
        elif bb["pct"] > 85: score -= 8
        elif bb["pct"] > 70: score -= 4
        if bb.get("squeeze"):
            if ema_bull: score += 6
            elif ema_bear: score -= 6

        # 5. Fibonacci (weight 10)
        fibo_trend = fibo.get("trend","?")
        fibo_zone  = fibo.get("zone","")
        if fibo.get("in_golden"):
            score += 10 if fibo_trend == "UPTREND" else -10
        if "NEAR_0.618" in fibo_zone and fibo_trend == "UPTREND":
            score += 7
        elif "NEAR_0.382" in fibo_zone and fibo_trend == "UPTREND":
            score += 4
        if "NEAR_0.382" in fibo_zone and fibo_trend == "DOWNTREND":
            score -= 7
        elif "NEAR_0.618" in fibo_zone and fibo_trend == "DOWNTREND":
            score -= 4

        # 6. Wyckoff (weight 12)
        wy_adj = wyc.get("score_adj", 0)
        if wyc.get("phase") == "TRANSITION":
            score += wy_adj * 0.3
        else:
            score += wy_adj * 0.7

        # 7. CVD (weight 10)
        cvd_adj = cvd_d.get("score_adj", 0)
        if cvd_d.get("divergence"):
            score += cvd_adj * 1.2
        else:
            score += cvd_adj * 0.7

        # 8. Volume Analysis (weight 10)
        vol_adj = vol_d.get("score_adj", 0)
        score += vol_adj * 0.5

        # 9. Stochastic RSI (weight 8)
        stoch = self.ind.stoch_rsi(closes)
        stoch_adj = stoch.get("score_adj", 0)
        if abs(stoch_adj) >= 10:
            if ((stoch_adj > 0 and rsi_v < 50) or
                (stoch_adj < 0 and rsi_v > 50)):
                score += stoch_adj * 0.8
            else:
                score += stoch_adj * 0.4

        # 10. Candlestick Patterns (weight 12)
        # Mô hình nến kinh điển — bổ sung tín hiệu kỹ thuật vi mô
        candle_adj  = candle.get("score_adj", 0)
        candle_str  = candle.get("strength", 0)
        if candle_str >= 30:
            if candle.get("confirm_long") or candle.get("confirm_short"):
                score += candle_adj * 1.0
            else:
                score += candle_adj * 0.6

        # 11. Market Structure (weight 10)
        ms_adj = mstruct.get("score_adj", 0)
        ms_str = mstruct.get("structure", "SIDEWAYS")
        if ms_str in ("UPTREND", "DOWNTREND"):
            score += ms_adj * 0.7
        elif mstruct.get("bos"):
            score += ms_adj * 1.0

        # 12. Elliott Wave (weight 12)
        score += elliott.get("score_adj", 0)
        score += fvg.get("score_adj", 0)
        score = max(5, min(95, score))

        # ══════════════════════════════════════════════════════
        # SIGNAL DIRECTION
        # ══════════════════════════════════════════════════════
        if bo["type"] == "BREAKOUT_UP" and bo["strength"] >= 70:
            direction = "LONG"
            score     = max(score, 72)
        elif bo["type"] == "BREAKOUT_DOWN" and bo["strength"] >= 70:
            direction = "SHORT"
            score     = min(score, 28)
        elif whale.get("detected") and whale["type"] == "WHALE_BUY":
            direction = "LONG"
            score     = max(score, 70)
        elif whale.get("detected") and whale["type"] == "WHALE_SELL":
            direction = "SHORT"
            score     = min(score, 30)
        elif score >= 67: direction = "LONG"
        elif score <= 33: direction = "SHORT"
        else:             direction = "WAIT"

        # Anti-noise filter
        if not is_trending and 38 <= score <= 62 and bo.get("type", "NONE") == "NONE":
            direction = "WAIT"

        # Candlestick conflict filter
        if interval in ("1h", "4h"):
            if direction == "LONG" and candle.get("confirm_short"):
                log.debug("Candle conflict: %s LONG bị filter bởi %s",
                          interval, candle.get("bear_patterns", []))
                if candle.get("strength", 0) >= 50:
                    score = min(score, 48)
                    direction = "WAIT"
            elif direction == "SHORT" and candle.get("confirm_long"):
                log.debug("Candle conflict: %s SHORT bị filter bởi %s",
                          interval, candle.get("bull_patterns", []))
                if candle.get("strength", 0) >= 50:
                    score = max(score, 52)
                    direction = "WAIT"

        return {
            "interval": interval, "direction": direction,
            "score": round(score, 1), "rsi": rsi_v,
            "macd": macd_d, "bb": bb,
            "ema": {"bull": ema_bull, "bear": ema_bear,
                    "e20": ema20, "e50": ema50, "e200": ema200},
            "fibo": fibo, "wyckoff": wyc, "cvd": cvd_d,
            "breakout": bo, "whale": whale, "volume": vol_d,
            "candle": candle, "market_structure": mstruct,
            "elliott": elliott,
            "fvg": fvg,
            "atr": round(atr, 4), "atr_pct": round(atr_pct, 3),
            "is_trending": is_trending, "price": price,
            "high": highs, "low": lows, "close": closes
        }

    def full_analysis(self, symbol):
        fetcher, atype = self._fetcher(symbol)
        log.info("📊 Phân tích %s [%s]...", symbol, atype)
        results = {}

        # Chạy song song 4 TF
        def _fetch_tf(tf):
            return tf, self.analyze_tf(symbol, tf, fetcher)

        futures = {self._executor.submit(_fetch_tf, tf): tf for tf in self.TIMEFRAMES}
        for fut in as_completed(futures, timeout=35):
            tf = futures[fut]
            try:
                tf_name, res = fut.result()
                results[tf_name] = res
            except Exception as e:
                log.warning("  TF %s lỗi: %s", tf, e)

        if not results:
            raise RuntimeError("Không lấy được dữ liệu " + symbol)

        # Penalty confidence khi thiếu TF quan trọng
        missing_important = [tf for tf in ("1h", "4h") if tf not in results]
        tf_penalty = len(missing_important) * 10

        tf_count = len(results)
        if tf_count < 2:
            raise RuntimeError(f"Quá ít TF ({tf_count}) cho {symbol}")

        price = list(results.values())[-1]["price"]
        oi_now, oi_delta = 0.0, 0.0
        funding = 0.0
        oi_signal, oi_desc = "N/A", "N/A"

        if atype == "CRYPTO":
            oi_now, oi_delta = self.crypto.open_interest(symbol)
            funding          = self.crypto.funding_rate(symbol)
            prev             = self._cache.get(symbol, price)
            self._cache[symbol] = price
            px_up  = price > prev
            oi_up  = oi_delta > 0.5
            oi_dn  = oi_delta < -0.5
            if   oi_up  and px_up:     oi_signal, oi_desc = "LONG_BUILD",    "Long mới vào — bullish"
            elif oi_up  and not px_up: oi_signal, oi_desc = "SHORT_BUILD",   "Short mới vào — bearish"
            elif oi_dn  and px_up:     oi_signal, oi_desc = "SHORT_SQUEEZE", "Short bị ép"
            elif oi_dn  and not px_up: oi_signal, oi_desc = "LONG_LIQ",      "Long bị thanh lý"
            else:                      oi_signal, oi_desc = "NEUTRAL",        "OI ổn định"

        mkt_open, mkt_note = True, ""
        if atype == "STOCK":
            mkt_open, mkt_note = self.stock.market_open()

        longs     = sum(1 for r in results.values() if r["direction"] == "LONG")
        shorts    = sum(1 for r in results.values() if r["direction"] == "SHORT")
        avg_score = sum(r["score"] for r in results.values()) / len(results)

        cvd_1h = results.get("1h", {}).get("cvd", {})
        bo_1h  = results.get("1h", {}).get("breakout", {})
        bo_4h  = results.get("4h", {}).get("breakout", {})
        wh_1h  = results.get("1h", {}).get("whale", {})
        wy_4h  = results.get("4h", {}).get("wyckoff", {})
        fi_1h  = results.get("1h", {}).get("fibo", {})
        vol_1h = results.get("1h", {}).get("volume", {})
        vol_4h = results.get("4h", {}).get("volume", {})

        smart = 0
        cvd_tr = cvd_1h.get("trend", "NEUTRAL")
        if cvd_tr == "BULLISH":       smart += 20
        elif cvd_tr == "BULLISH_DIV": smart += 15
        elif cvd_tr == "BEARISH":     smart -= 20
        elif cvd_tr == "BEARISH_DIV": smart -= 15
        if wy_4h.get("bias") == "BULLISH":   smart += 10
        elif wy_4h.get("bias") == "BEARISH": smart -= 10
        if fi_1h.get("trend") == "UPTREND":     smart += 8
        elif fi_1h.get("trend") == "DOWNTREND": smart -= 8
        if wh_1h.get("detected"):
            smart += 25 if wh_1h["type"] == "WHALE_BUY" else -25
        vol_smart = (vol_1h.get("score_adj", 0) + vol_4h.get("score_adj", 0)) * 0.5
        smart += vol_smart
        if vol_1h.get("buy_confirm")  and vol_4h.get("buy_confirm"):  smart += 15
        if vol_1h.get("sell_confirm") and vol_4h.get("sell_confirm"): smart -= 15

        combined = avg_score * 0.6 + (50 + smart) * 0.4
        combined = max(0, min(100, combined))

        log.info("  TF=%.1f Smart=%+.1f Combined=%.1f L:%d S:%d",
                 avg_score, smart, combined, longs, shorts)

        # Scale down threshold khi thiếu TF
        min_long_tfs  = min(3, tf_count - 1)
        min_short_tfs = min(3, tf_count - 1)

        final, conf = None, 50.0
        for tf_n, bo in [("1H", bo_1h), ("4H", bo_4h)]:
            if bo.get("type") in ("BREAKOUT_UP","BREAKOUT_DOWN") and bo.get("strength",0) >= 70:
                final = "LONG" if bo["type"] == "BREAKOUT_UP" else "SHORT"
                conf  = min(95, 70 + bo["strength"] * 0.25)
                log.info("🚨 Breakout override [%s]", tf_n)
                break

        if final is None:
            long_ok  = longs >= min_long_tfs  and combined >= 62 and smart >= -10
            short_ok = shorts >= min_short_tfs and combined <= 38 and smart <= 10
            if   long_ok  or combined >= 68: final = "LONG";  conf = round(min(95, combined), 1)
            elif short_ok or combined <= 32: final = "SHORT"; conf = round(min(95, 100-combined), 1)
            else:                             final = "WAIT";  conf = round(min(95, max(30, combined)), 1)
            if cvd_tr in ("BEARISH_DIV","BULLISH_DIV") and abs(smart) < 20:
                conf = round(conf * 0.85, 1)
                if conf < 55:
                    final = "WAIT"

        # Áp dụng TF penalty vào confidence
        if tf_penalty > 0:
            conf = round(max(30, conf - tf_penalty), 1)
            log.warning("  ⚠️ Thiếu TF %s → penalty -%d%% → conf=%.1f%%",
                        missing_important, tf_penalty, conf)

        # ── 12-Agent L2 Orderbook and Liquidity Analysis ─────────────────
        ob_data = {"detected": False}
        sweep_data = {"detected": False}

        if atype == "CRYPTO":
            try:
                # Lấy dữ liệu Sổ lệnh L2
                ob_raw = fetcher.order_book(symbol, depth=30)
                if ob_raw.get("ok"):
                    best_bid_wall = ob_raw["bid_walls"][0] if ob_raw.get("bid_walls") else {"price": 0, "usd": 0}
                    best_ask_wall = ob_raw["ask_walls"][0] if ob_raw.get("ask_walls") else {"price": 0, "usd": 0}
                    
                    ob_data = {
                        "detected": True,
                        "ratio": ob_raw["ratio"],
                        "imbalance": ob_raw["imbalance"],
                        "spread_pct": ob_raw["spread_pct"],
                        "support_wall": best_bid_wall["price"],
                        "support_wall_usd": best_bid_wall["usd"],
                        "resist_wall": best_ask_wall["price"],
                        "resist_wall_usd": best_ask_wall["usd"],
                    }
                
                # Lấy dữ liệu thanh lý
                liq_raw = fetcher.liquidation_levels(symbol, price)
                
                # Phân tích Bẫy Thanh Khoản (Liquidity Sweep)
                tf_to_check = results.get("1h") or results.get("15m")
                if tf_to_check:
                    mstruct = tf_to_check.get("market_structure", {})
                    swing_high = mstruct.get("last_swing_high", 0)
                    swing_low = mstruct.get("last_swing_low", 0)
                    
                    high_price = tf_to_check.get("high", [0])[-1]
                    low_price = tf_to_check.get("low", [0])[-1]
                    close_price = tf_to_check.get("price", price)
                    
                    detected_sweep = False
                    sweep_type = "NONE"
                    sweep_p = 0.0
                    
                    # Bullish Liquidity Sweep (Spring): Giá quét dưới Swing Low cũ nhưng đóng cửa trên Swing Low cũ
                    if swing_low > 0 and low_price < swing_low and close_price > swing_low:
                        detected_sweep = True
                        sweep_type = "BULLISH_SWEEP"
                        sweep_p = low_price
                    # Bearish Liquidity Sweep (UTAD): Giá quét trên Swing High cũ nhưng đóng cửa dưới Swing High cũ
                    elif swing_high > 0 and high_price > swing_high and close_price < swing_high:
                        detected_sweep = True
                        sweep_type = "BEARISH_SWEEP"
                        sweep_p = high_price
                    
                    if detected_sweep:
                        sweep_data = {
                            "detected": True,
                            "type": sweep_type,
                            "price": sweep_p,
                            "cascade_risk": liq_raw.get("cascade_risk", False),
                            "dominant_side": liq_raw.get("dominant_side", "NEUTRAL")
                        }
            except Exception as e:
                log.warning("⚠️ Lỗi phân tích L2 Orderbook/Liquidity cho %s: %s", symbol, e)

        # ATR-based SL/TP
        atr_1h     = results.get("1h", {}).get("atr", price * 0.01)
        atr_pct_1h = results.get("1h", {}).get("atr_pct", 1.0)
        sl_atr_pct = max(0.8, min(2.5, atr_pct_1h * 1.5))
        tp1_pct    = sl_atr_pct * 2.0
        tp2_pct    = sl_atr_pct * 3.5
        fibo4l     = results.get("4h", {}).get("fibo", {}).get("levels", {})

        # ATR-based defaults
        atr_tp1 = round(price * (1 + tp1_pct / 100), 2)
        atr_tp2 = round(price * (1 + tp2_pct / 100), 2)
        atr_tp1_s = round(price * (1 - tp1_pct / 100), 2)
        atr_tp2_s = round(price * (1 - tp2_pct / 100), 2)

        fibo4h_trend = results.get("4h", {}).get("fibo", {}).get("trend", "")

        if final == "LONG":
            sl = round(price * (1 - sl_atr_pct / 100), 2)
            if fibo4h_trend == "UPTREND":
                f272 = fibo4l.get("1.272")
                f618 = fibo4l.get("1.618")
                if (f272 and f618 and
                        f272 > price * 1.005 and
                        f618 > price * 1.005 and
                        f618 > f272):
                    tp1 = round(f272, 2)
                    tp2 = round(f618, 2)
                else:
                    tp1 = atr_tp1
                    tp2 = atr_tp2
            else:
                tp1 = atr_tp1
                tp2 = atr_tp2

        elif final == "SHORT":
            sl  = round(price * (1 + sl_atr_pct / 100), 2)
            if fibo4h_trend == "DOWNTREND":
                f272 = fibo4l.get("1.272")
                f618 = fibo4l.get("1.618")
                if (f272 and f618 and
                        f272 < price * 0.995 and
                        f618 < price * 0.995 and
                        f618 < f272):
                    tp1 = round(f272, 2)
                    tp2 = round(f618, 2)
                else:
                    tp1 = atr_tp1_s
                    tp2 = atr_tp2_s
            else:
                tp1 = atr_tp1_s
                tp2 = atr_tp2_s

        else:  # WAIT
            sl  = round(price * (1 - sl_atr_pct / 100), 2)
            tp1 = atr_tp1
            tp2 = atr_tp2

        # Đảm bảo thứ tự TP luôn đúng
        if final == "LONG":
            if tp2 <= tp1:
                log.warning("⚠️ TP2(%.4f) <= TP1(%.4f) cho LONG %s — dùng ATR", tp2, tp1, symbol)
                tp1 = atr_tp1
                tp2 = atr_tp2
            if tp1 <= price:
                tp1 = atr_tp1
            if tp2 <= tp1:
                tp2 = round(tp1 * 1.015, 2)

        elif final == "SHORT":
            if tp2 >= tp1:
                log.warning("⚠️ TP2(%.4f) >= TP1(%.4f) cho SHORT %s — dùng ATR", tp2, tp1, symbol)
                tp1 = atr_tp1_s
                tp2 = atr_tp2_s
            if tp1 >= price:
                tp1 = atr_tp1_s
            if tp2 >= tp1:
                tp2 = round(tp1 * 0.985, 2)

        rr_ratio = round(tp1_pct / sl_atr_pct, 2) if sl_atr_pct > 0 else 2.0

        candle_1h = results.get("1h", {}).get("candle", {})
        candle_4h = results.get("4h", {}).get("candle", {})
        ms_1h     = results.get("1h", {}).get("market_structure", {})
        ms_4h     = results.get("4h", {}).get("market_structure", {})

        all_bull_patterns = (candle_1h.get("bull_patterns", []) +
                             candle_4h.get("bull_patterns", []))
        all_bear_patterns = (candle_1h.get("bear_patterns", []) +
                             candle_4h.get("bear_patterns", []))

        candle_summary = {
            "bias":           candle_4h.get("bias", candle_1h.get("bias", "NEUTRAL")),
            "bull_patterns":  all_bull_patterns,
            "bear_patterns":  all_bear_patterns,
            "confirm_long":   candle_4h.get("confirm_long") or candle_1h.get("confirm_long"),
            "confirm_short":  candle_4h.get("confirm_short") or candle_1h.get("confirm_short"),
            "strength":       max(candle_4h.get("strength", 0), candle_1h.get("strength", 0)),
        }

        if final == "LONG" and candle_summary["confirm_long"]:
            conf = round(min(95, conf * 1.05), 1)
        elif final == "SHORT" and candle_summary["confirm_short"]:
            conf = round(min(95, conf * 1.05), 1)


        # ─── XÁC SUẤT (BAYES) & KỲ VỌNG TOÁN HỌC (EV) ───
        base_odds = 0.818 # Base win rate ~ 45%
        likelihood = 1.0
        p_win = 0.45
        ev_ratio = 0.0

        if final == "LONG":
            if cvd_tr == "BULLISH": likelihood *= 1.3
            elif cvd_tr == "BULLISH_DIV": likelihood *= 1.5
            elif cvd_tr in ("BEARISH", "BEARISH_DIV"): likelihood *= 0.6
            
            if wy_4h.get("bias") == "BULLISH": likelihood *= 1.2
            elif wy_4h.get("bias") == "BEARISH": likelihood *= 0.7
            
            if wh_1h.get("detected") and wh_1h.get("type") == "WHALE_BUY": likelihood *= 1.4
            elif wh_1h.get("detected") and wh_1h.get("type") == "WHALE_SELL": likelihood *= 0.6
            
            if bo_1h.get("type") == "BREAKOUT_UP": likelihood *= 1.3
            
            if ob_data.get("detected") and ob_data.get("imbalance", 0) > 1.5: likelihood *= 1.15
            elif ob_data.get("detected") and ob_data.get("imbalance", 0) < -1.5: likelihood *= 0.85
            
            if sweep_data.get("detected") and sweep_data.get("type") == "BULLISH_SWEEP": likelihood *= 1.3
            
            if candle_summary["confirm_long"]: likelihood *= 1.2
            
        elif final == "SHORT":
            if cvd_tr == "BEARISH": likelihood *= 1.3
            elif cvd_tr == "BEARISH_DIV": likelihood *= 1.5
            elif cvd_tr in ("BULLISH", "BULLISH_DIV"): likelihood *= 0.6
            
            if wy_4h.get("bias") == "BEARISH": likelihood *= 1.2
            elif wy_4h.get("bias") == "BULLISH": likelihood *= 0.7
            
            if wh_1h.get("detected") and wh_1h.get("type") == "WHALE_SELL": likelihood *= 1.4
            elif wh_1h.get("detected") and wh_1h.get("type") == "WHALE_BUY": likelihood *= 0.6
            
            if bo_1h.get("type") == "BREAKOUT_DOWN": likelihood *= 1.3
            
            if ob_data.get("detected") and ob_data.get("imbalance", 0) < -1.5: likelihood *= 1.15
            elif ob_data.get("detected") and ob_data.get("imbalance", 0) > 1.5: likelihood *= 0.85
            
            if sweep_data.get("detected") and sweep_data.get("type") == "BEARISH_SWEEP": likelihood *= 1.3
            
            if candle_summary["confirm_short"]: likelihood *= 1.2

        if final in ("LONG", "SHORT"):
            bayes_odds = base_odds * likelihood
            p_win = bayes_odds / (1 + bayes_odds)
            
            reward_tp1_ratio = (abs(tp1 - price) / price) / (sl_atr_pct / 100) if sl_atr_pct > 0 else 2.0
            reward_tp2_ratio = (abs(tp2 - price) / price) / (sl_atr_pct / 100) if sl_atr_pct > 0 else 3.5
            avg_reward_ratio = (reward_tp1_ratio + reward_tp2_ratio) / 2
            
            ev_ratio = (p_win * avg_reward_ratio) - ((1 - p_win) * 1.0)
            
            log.info("  [Bayes EV] %s %s: P(win)=%.1f%%, EV_Ratio=%.2f (Likelihood=%.2f)",
                     final, symbol, p_win * 100, ev_ratio, likelihood)
            
            if ev_ratio < 0.15:
                log.warning("  ⚠️ EV(%.2f) quá thấp, hạ cấp thành WAIT", ev_ratio)
                final = "WAIT"
                conf = round(conf * 0.8, 1)
            elif ev_ratio > 0.5:
                conf = round(min(95, conf + (ev_ratio * 5)), 1)
                
        ev_data = {
            "p_win": round(p_win * 100, 1),
            "ev_ratio": round(ev_ratio, 2),
            "likelihood": round(likelihood, 2)
        }
        return {
            "symbol": symbol, "asset_type": atype,
            "price": price, "final": final, "confidence": conf,
            "longs": longs, "shorts": shorts, "timeframes": results,
            "plan": {"entry": price, "sl": sl, "tp1": tp1, "tp2": tp2},
            "wyckoff": wy_4h, "fibo": fi_1h, "cvd": cvd_1h,
            "breakout": bo_1h, "whale": wh_1h,
            "volume_1h": vol_1h, "volume_4h": vol_4h,
            "candle": candle_summary,
            "elliott_4h": results.get("4h", {}).get("elliott", {}),
            "market_structure_1h": ms_1h,
            "market_structure_4h": ms_4h,
            "oi_signal": oi_signal, "oi_desc": oi_desc,
            "oi_delta": round(oi_delta, 3), "funding": round(funding, 4),
            "mkt_open": mkt_open, "mkt_note": mkt_note,
            "rr_ratio": rr_ratio, "sl_pct": round(sl_atr_pct, 2),
            "tf_count": tf_count,
            "orderbook": ob_data,
            "liquidity_sweep": sweep_data,
            "timestamp": datetime.now().strftime("%d/%m %H:%M"),
            "bayes_ev": ev_data,
        }
`,
  "fetcher.py": `# ═══════════════════════════════════════════════════════════
# 1. CRYPTO FETCHER — v6.1 (Session Pool + Retry + Safe Fallback)
# ═══════════════════════════════════════════════════════════
from datetime import datetime
import logging
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("analyzer.fetcher")


def _make_session(retries=2, backoff=0.3, timeout=None):
    """
    Tạo requests.Session với connection pooling và retry tự động.
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=4,
        pool_maxsize=10,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class CryptoFetcher:
    """
    Crypto data — Ưu tiên Bybit, Fallback sang BingX.
    """
    BGX = "https://open-api.bingx.com/openApi"
    BBT = "https://api.bybit.com"
    HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    BGX_IV = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    BBT_IV = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}

    def __init__(self):
        self._session = _make_session(retries=2, backoff=0.3)

    def _bgx(self, s):
        s = str(s).strip().upper()
        if '-' in s: return s
        if s.endswith('USDT'):
            return s[:-4] + '-USDT'
        return s

    def _bbt(self, s):
        s = str(s).strip().upper()
        return s.replace('-', '')

    def _tbvol_ratio(self, closes):
        """
        Tính ratio taker buy volume động thay vì hardcode 52%.
        """
        if len(closes) < 6:
            return 0.52
        recent = closes[-1]
        prev   = closes[-6]
        if prev <= 0:
            return 0.52
        pct_change = (recent - prev) / prev
        ratio = 0.51 + pct_change * 3.5
        return round(max(0.40, min(0.62, ratio)), 3)

    def price(self, symbol):
        """Lấy giá Last Price"""
        if not symbol:
            return 0.0

        bbt_sym = self._bbt(symbol)
        bgx_sym = self._bgx(symbol)

        # Bybit
        try:
            r = self._session.get(
                self.BBT + "/v5/market/tickers",
                params={"category": "linear", "symbol": bbt_sym},
                headers=self.HDR, timeout=6)
            if r.status_code == 200:
                d = r.json()
                if d.get("retCode") == 0:
                    lst = d.get("result", {}).get("list", [])
                    if lst and lst[0].get("symbol") == bbt_sym:
                        p = float(lst[0].get("lastPrice", 0))
                        if p > 0:
                            return round(p, 4)
                    else:
                        log.warning("Bybit sai symbol cho %s", bbt_sym)
        except Exception as e:
            log.debug("Bybit ticker err %s: %s", bbt_sym, e)

        # BingX Fallback
        try:
            r = self._session.get(
                self.BGX + "/swap/v2/quote/ticker",
                params={"symbol": bgx_sym},
                headers=self.HDR, timeout=6)
            if r.status_code == 200:
                d = r.json()
                if d.get("code") == 0 and d.get("data"):
                    data = d["data"]
                    if isinstance(data, dict) and data.get("symbol") == bgx_sym:
                        p = float(data.get("lastPrice") or data.get("markPrice") or 0)
                        if p > 0:
                            return round(p, 4)
                    elif isinstance(data, list):
                        for item in data:
                            if item.get("symbol") == bgx_sym:
                                p = float(item.get("lastPrice", 0))
                                if p > 0:
                                    return round(p, 4)
        except Exception as e:
            log.debug("BingX ticker err %s: %s", bgx_sym, e)

        log.error("KHÔNG THỂ LẤY GIÁ CHO %s", symbol)
        return 0.0

    def klines(self, symbol, interval, limit=150):
        """Lấy Klines — Bybit trước, BingX fallback"""
        if not symbol:
            return None

        bbt_sym = self._bbt(symbol)
        bgx_sym = self._bgx(symbol)
        bbt_iv  = self.BBT_IV.get(interval, "60")
        bgx_iv  = self.BGX_IV.get(interval, "1h")

        # Bybit Klines
        try:
            r = self._session.get(
                self.BBT + "/v5/market/kline",
                params={"category": "linear", "symbol": bbt_sym,
                        "interval": bbt_iv, "limit": limit},
                headers=self.HDR, timeout=10)
            if r.status_code == 200:
                d = r.json()
                if d.get("retCode") == 0:
                    raw = list(reversed(d["result"]["list"]))
                    if len(raw) >= 20:
                        closes = [float(c[4]) for c in raw]
                        vols   = [max(float(c[5]), 0.001) for c in raw]
                        tbv_ratio = self._tbvol_ratio(closes)
                        return {
                            "open":          [float(c[1]) for c in raw],
                            "high":          [float(c[2]) for c in raw],
                            "low":           [float(c[3]) for c in raw],
                            "close":         closes,
                            "volume":        vols,
                            "taker_buy_vol": [v * tbv_ratio for v in vols],
                        }
        except Exception as e:
            log.debug("Bybit klines err %s %s: %s", bbt_sym, interval, e)

        # BingX Klines Fallback
        try:
            r = self._session.get(
                self.BGX + "/swap/v3/quote/klines",
                params={"symbol": bgx_sym, "interval": bgx_iv, "limit": limit},
                headers=self.HDR, timeout=10)
            if r.status_code == 200:
                d = r.json()
                if d.get("code") == 0:
                    data = d.get("data", [])
                    if len(data) >= 20:
                        closes = [float(c.get("close", 0)) for c in data]
                        vols   = [max(float(c.get("volume", 0)), 0.001) for c in data]
                        tbv_ratio = self._tbvol_ratio(closes)
                        tbvols = []
                        for i, c in enumerate(data):
                            tbv = c.get("takerBuyVolume") or c.get("taker_buy_volume")
                            if tbv and float(tbv) > 0:
                                tbvols.append(float(tbv))
                            else:
                                tbvols.append(vols[i] * tbv_ratio)
                        return {
                            "open":          [float(c.get("open", closes[i])) for i, c in enumerate(data)],
                            "high":          [float(c.get("high", closes[i])) for i, c in enumerate(data)],
                            "low":           [float(c.get("low",  closes[i])) for i, c in enumerate(data)],
                            "close":         closes,
                            "volume":        vols,
                            "taker_buy_vol": tbvols,
                        }
        except Exception as e:
            log.debug("BingX klines err %s %s: %s", bgx_sym, interval, e)

        log.error("Không lấy được klines cho %s [%s]", symbol, interval)
        return None

    def funding_rate(self, symbol):
        if not symbol:
            return 0.0
        try:
            r = self._session.get(
                self.BBT + "/v5/market/tickers",
                params={"category": "linear", "symbol": self._bbt(symbol)},
                headers=self.HDR, timeout=5)
            d = r.json()
            if d.get("retCode") == 0:
                lst = d.get("result", {}).get("list", [])
                if lst and lst[0].get("symbol") == self._bbt(symbol):
                    return float(lst[0].get("fundingRate", 0)) * 100
        except Exception:
            pass
        return 0.0

    def open_interest(self, symbol):
        if not symbol:
            return 0.0, 0.0
        try:
            r = self._session.get(
                self.BBT + "/v5/market/open-interest",
                params={"category": "linear", "symbol": self._bbt(symbol),
                        "intervalTime": "1h", "limit": 3},
                headers=self.HDR, timeout=5)
            d = r.json()
            if d.get("retCode") == 0:
                lst = d.get("result", {}).get("list", [])
                if len(lst) >= 2:
                    a = float(lst[0].get("openInterest", 0))
                    b = float(lst[1].get("openInterest", 0))
                    return a, round((a - b) / b * 100, 3) if b else 0
        except Exception:
            pass
        return 0.0, 0.0

    def order_book(self, symbol: str, depth: int = 50) -> dict:
        """
        Lấy Order Book (Depth) từ Bybit → BingX fallback.
        """
        EMPTY = {
            "bids": [], "asks": [],
            "bid_total": 0.0, "ask_total": 0.0,
            "ratio": 1.0, "imbalance": 0.0,
            "spread_pct": 0.0, "best_bid": 0.0, "best_ask": 0.0,
            "bid_walls": [], "ask_walls": [],
            "mid_price": 0.0, "ok": False,
        }
        if not symbol:
            return EMPTY

        bbt_sym = self._bbt(symbol)
        bgx_sym = self._bgx(symbol)

        # Bybit Order Book
        try:
            r = self._session.get(
                self.BBT + "/v5/market/orderbook",
                params={"category": "linear", "symbol": bbt_sym, "limit": depth},
                headers=self.HDR, timeout=8)
            if r.status_code == 200:
                d = r.json()
                if d.get("retCode") == 0:
                    raw_bids = [[float(p), float(q)] for p, q in d["result"].get("b", [])]
                    raw_asks = [[float(p), float(q)] for p, q in d["result"].get("a", [])]
                    return self._parse_orderbook(raw_bids, raw_asks)
        except Exception as e:
            log.debug("Bybit orderbook %s: %s", bbt_sym, e)

        # BingX Fallback
        try:
            r = self._session.get(
                self.BGX + "/swap/v2/quote/depth",
                params={"symbol": bgx_sym, "limit": depth},
                headers=self.HDR, timeout=8)
            if r.status_code == 200:
                d = r.json()
                if d.get("code") == 0:
                    data = d.get("data", {})
                    raw_bids = [[float(p), float(q)] for p, q in data.get("bids", [])]
                    raw_asks = [[float(p), float(q)] for p, q in data.get("asks", [])]
                    return self._parse_orderbook(raw_bids, raw_asks)
        except Exception as e:
            log.debug("BingX orderbook %s: %s", bgx_sym, e)

        return EMPTY

    @staticmethod
    def _parse_orderbook(raw_bids: list, raw_asks: list) -> dict:
        if not raw_bids or not raw_asks:
            return {"bids":[], "asks":[], "bid_total":0, "ask_total":0,
                    "ratio":1.0, "imbalance":0.0, "spread_pct":0.0,
                    "best_bid":0.0, "best_ask":0.0, "bid_walls":[], "ask_walls":[],
                    "mid_price":0.0, "ok":False}

        bids = sorted(raw_bids, key=lambda x: x[0], reverse=True)
        asks = sorted(raw_asks, key=lambda x: x[0])

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0
        mid      = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
        spread   = round((best_ask - best_bid) / mid * 100, 4) if mid else 0.0

        bid_total = sum(p * q for p, q in bids)
        ask_total = sum(p * q for p, q in asks)
        total     = bid_total + ask_total
        ratio     = round(bid_total / ask_total, 3) if ask_total > 0 else 1.0
        imbalance = round((bid_total - ask_total) / total * 100, 1) if total > 0 else 0.0

        def find_walls(orders, threshold_mult=2.5):
            if len(orders) < 3:
                return []
            sizes  = [q for _, q in orders]
            avg_q  = sum(sizes) / len(sizes)
            cutoff = avg_q * threshold_mult
            walls  = []
            for price, qty in orders:
                if qty >= cutoff:
                    walls.append({"price": round(price, 2),
                                  "qty": round(qty, 4),
                                  "usd": round(price * qty, 0),
                                  "mult": round(qty / avg_q, 1)})
            return sorted(walls, key=lambda x: x["usd"], reverse=True)[:5]

        bid_walls = find_walls(bids)
        ask_walls = find_walls(asks)

        return {
            "bids":      bids[:20],
            "asks":      asks[:20],
            "best_bid":  round(best_bid, 4),
            "best_ask":  round(best_ask, 4),
            "mid_price": round(mid, 4),
            "spread_pct": spread,
            "bid_total": round(bid_total, 0),
            "ask_total": round(ask_total, 0),
            "ratio":     ratio,
            "imbalance": imbalance,
            "bid_walls": bid_walls,
            "ask_walls": ask_walls,
            "ok":        True,
        }

    def liquidation_levels(self, symbol: str, current_price: float) -> dict:
        """
        Tính các ngưỡng thanh lý theo đòn bẩy.
        """
        EMPTY = {"long_liq_levels": [], "short_liq_levels": [],
                 "dominant_side": "NEUTRAL", "cascade_risk": False,
                 "long_ratio": 0.5, "short_ratio": 0.5, "ok": False}

        if not symbol or not current_price:
            return EMPTY

        try:
            r = self._session.get(
                self.BBT + "/v5/market/account-ratio",
                params={"category": "linear", "symbol": self._bbt(symbol),
                        "period": "5min", "limit": 1},
                headers=self.HDR, timeout=6)
            if r.status_code == 200:
                d = r.json()
                if d.get("retCode") == 0:
                    lst = d.get("result", {}).get("list", [])
                    if lst:
                        buy_ratio  = float(lst[0].get("buyRatio",  0.5))
                        sell_ratio = float(lst[0].get("sellRatio", 0.5))
                    else:
                        buy_ratio = sell_ratio = 0.5
                else:
                    buy_ratio = sell_ratio = 0.5
            else:
                buy_ratio = sell_ratio = 0.5
        except Exception:
            buy_ratio = sell_ratio = 0.5

        dominant = "LONG" if buy_ratio > sell_ratio else "SHORT"

        leverages = [5, 10, 20, 50, 100]
        p = current_price

        long_liqs  = []
        short_liqs = []
        for lev in leverages:
            liq_long  = round(p * (1 - 0.9 / lev), 2)
            liq_short = round(p * (1 + 0.9 / lev), 2)
            long_liqs.append({"leverage": lev, "price": liq_long,
                               "distance_pct": round((p - liq_long) / p * 100, 2)})
            short_liqs.append({"leverage": lev, "price": liq_short,
                                "distance_pct": round((liq_short - p) / p * 100, 2)})

        cascade_levels = [l["price"] for l in long_liqs if l["leverage"] in (20, 50)]
        cascade_risk   = any(abs(p - liq) / p < 0.02 for liq in cascade_levels)

        return {
            "long_liq_levels":  long_liqs,
            "short_liq_levels": short_liqs,
            "dominant_side":    dominant,
            "cascade_risk":     cascade_risk,
            "long_ratio":       round(buy_ratio, 3),
            "short_ratio":      round(sell_ratio, 3),
            "ok":               True,
        }


# ═══════════════════════════════════════════════════════════
# 2. STOCK / GOLD FETCHER — v6.1
# ═══════════════════════════════════════════════════════════

class StockFetcher:
    HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Accept": "application/json",
           "Referer": "https://finance.yahoo.com/"}
    CGK    = "https://api.coingecko.com/api/v3"
    # Sửa GC%3DF thành GC=F để API requests không bị lỗi encode
    YAHOO  = {"TSLA": "TSLA", "NVDA": "NVDA", "SPY": "SPY", "QQQ": "QQQ", "NCCOGOLD2USD-USDT": "GC=F"}
    YH_IV  = {"15m": "15m", "1h": "1h", "4h": "1h", "1d": "1d"}
    YH_RNG = {"15m": "5d", "1h": "30d", "4h": "30d", "1d": "6mo"}
    PLIM   = {"TSLA": (10, 5000), "NVDA": (10, 5000),
               "SPY": (100, 1500), "QQQ": (100, 1500), "NCCOGOLD2USD-USDT": (1500, 6000)}

    _SYNTHETIC_PRICES = {"NCCOGOLD2USD-USDT": 2350.0, "SPY": 540.0, "TSLA": 220.0, "NVDA": 120.0}

    def __init__(self):
        self._session = _make_session(retries=2, backoff=0.5)

    def price(self, symbol):
        lo, hi = self.PLIM.get(symbol, (0, 1e9))

        # GOLD
        if symbol == "NCCOGOLD2USD-USDT":
            try:
                r = self._session.get(
                    self.CGK + "/simple/price",
                    params={"ids": "pax-gold,tether-gold", "vs_currencies": "usd"},
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                r.raise_for_status()
                d = r.json()
                for tok in ["pax-gold", "tether-gold"]:
                    p = float(d.get(tok, {}).get("usd", 0))
                    if lo < p < hi:
                        log.info("Gold CoinGecko %s: \$%.2f", tok, p)
                        return round(p, 2)
            except Exception as e:
                log.warning("CoinGecko gold: %s", e)

            for base in ["query1", "query2"]:
                try:
                    r = self._session.get(
                        f"https://{base}.finance.yahoo.com/v8/finance/chart/GC=F"
                        "?interval=1m&range=1d",
                        headers=self.HDR, timeout=10)
                    if r.ok:
                        j = r.json()
                        # Kiểm tra an toàn xem có "result" không
                        if j.get("chart", {}).get("result"):
                            data = j["chart"]["result"][0]
                            meta = data.get("meta", {})
                            for key in ["regularMarketPrice", "chartPreviousClose"]:
                                p = float(meta.get(key, 0))
                                if lo < p < hi:
                                    log.info("Gold Yahoo %s [%s]: \$%.2f", base, key, p)
                                    return round(p, 2)
                            closes = [c for c in data["indicators"]["quote"][0].get("close", [])
                                      if c and lo < float(c) < hi]
                            if closes:
                                return round(closes[-1], 2)
                except Exception as e:
                    log.warning("Yahoo GC=F %s: %s", base, e)

            return 0.0

        # STOCKS
        ticker = self.YAHOO.get(symbol, symbol)
        for base in ["query1", "query2"]:
            try:
                r = self._session.get(
                    f"https://{base}.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d",
                    headers=self.HDR, timeout=10)
                if r.ok:
                    j = r.json()
                    # Kiểm tra an toàn xem có "result" không
                    if j.get("chart", {}).get("result"):
                        data = j["chart"]["result"][0]
                        meta = data.get("meta", {})
                        p = float(meta.get("regularMarketPrice", 0))
                        if lo < p < hi:
                            return round(p, 2)
                        closes = [c for c in data["indicators"]["quote"][0].get("close", [])
                                  if c and lo < float(c) < hi]
                        if closes:
                            return round(closes[-1], 2)
            except Exception as e:
                log.warning("Yahoo price %s %s: %s", symbol, base, e)

        return 0.0

    def klines(self, symbol, interval, limit=100):
        lo, hi  = self.PLIM.get(symbol, (0, 1e9))
        ticker  = self.YAHOO.get(symbol, symbol)
        yh_iv   = self.YH_IV.get(interval, "1h")
        yh_rng  = self.YH_RNG.get(interval, "30d")

        for base in ["query1", "query2"]:
            try:
                r = self._session.get(
                    f"https://{base}.finance.yahoo.com/v8/finance/chart/{ticker}"
                    f"?interval={yh_iv}&range={yh_rng}",
                    headers=self.HDR, timeout=15)
                if r.status_code != 200:
                    continue
                    
                j = r.json()
                # Kiểm tra an toàn để fix lỗi sập NoneType
                if not j.get("chart", {}).get("result"):
                    continue
                    
                q = j["chart"]["result"][0]["indicators"]["quote"][0]

                def clean(lst):
                    return [float(x) for x in lst if x is not None and lo < float(x) < hi]

                closes  = clean(q.get("close",  []))
                highs   = clean(q.get("high",   []))
                lows    = clean(q.get("low",    []))
                opens   = clean(q.get("open",   []))
                volumes = [float(x) if x else 1.0 for x in q.get("volume", [])]

                if len(closes) < 20:
                    continue

                n = min(len(closes), len(highs), len(lows), len(opens), len(volumes), limit)
                c = closes[-n:]
                h = highs[-n:]   if len(highs)   >= n else c
                l = lows[-n:]
                o = opens[-n:]   if len(opens)   >= n else c
                v = volumes[-n:] if len(volumes) >= n else [1.0] * n

                return {"open": o, "high": h, "low": l, "close": c,
                        "volume": v, "taker_buy_vol": [x * 0.52 for x in v]}
            except Exception as e:
                log.warning("Yahoo klines %s %s %s: %s", symbol, interval, base, e)

        # Fallback synthetic klines
        is_open, _ = self.market_open() if symbol != "NCCOGOLD2USD-USDT" else self.is_gold_open()
        if is_open:
            log.error("🚫 Yahoo FAIL khi thị trường đang MỞ cho %s [%s] — bỏ qua TF này", symbol, interval)
            return None

        import random
        p = self.price(symbol)
        if p <= 0:
            p = self._SYNTHETIC_PRICES.get(symbol, 100.0)
        c = [p * (1 + random.uniform(-0.003, 0.003)) for _ in range(limit)]
        c[-1] = p
        v = [random.uniform(1000, 5000) for _ in range(limit)]
        return {"open": c[:], "high": [x * 1.005 for x in c],
                "low": [x * 0.995 for x in c], "close": c,
                "volume": v, "taker_buy_vol": [x * 0.52 for x in v]}

    def market_open(self):
        from datetime import timezone, timedelta
        et  = timezone(timedelta(hours=-4))
        now = datetime.now(et)
        wd, h, m = now.weekday(), now.hour, now.minute
        if wd >= 5:
            return False, "📴 Cuối tuần — thị trường đóng"
        if h < 9 or (h == 9 and m < 30):
            return False, "⏰ Pre-market (mở lúc 9:30 ET)"
        if h >= 16:
            return False, "📴 After-hours (đóng lúc 16:00 ET)"
        return True, "🟢 NYSE/NASDAQ đang mở"

    def is_gold_open(self):
        from datetime import timezone, timedelta
        et  = timezone(timedelta(hours=-4))
        now = datetime.now(et)
        wd, h = now.weekday(), now.hour
        if wd == 5:
            return False, "📴 Gold đóng cửa (Thứ 7)"
        if wd == 6 and h < 18:
            return False, "📴 Gold mở lại CN 18:00 ET"
        if wd == 4 and h >= 17:
            return False, "📴 Gold đóng từ T6 17:00 ET"
        if h == 17:
            return False, "⏸️ Gold break 17:00–18:00 ET"
        return True, "🟡 Gold Futures đang giao dịch"`,
  "llm_agents.py": `from analyzer.config import Config
from datetime import datetime, timedelta
import logging
import requests
import time
import re

cfg = Config()
log = logging.getLogger("analyzer.llm_agents")


class _CircuitBreaker:
    MAX_FAILS = 3
    COOLDOWN  = 600

    def __init__(self):
        self._fails    : dict[str, int]   = {}
        self._down_until: dict[str, float] = {}

    def is_up(self, name: str) -> bool:
        t = self._down_until.get(name, 0)
        if t and time.time() < t:
            return False
        if t and time.time() >= t:
            self._fails.pop(name, None)
            self._down_until.pop(name, None)
            log.info("🔄 [%s] Circuit breaker RESET — thử lại", name)
        return True

    def ok(self, name: str):
        self._fails[name] = 0
        self._down_until.pop(name, None)

    def fail(self, name: str):
        n = self._fails.get(name, 0) + 1
        self._fails[name] = n
        if n >= self.MAX_FAILS:
            self._down_until[name] = time.time() + self.COOLDOWN
            log.warning("🔴 [%s] Circuit breaker OPEN — skip %ds", name, self.COOLDOWN)

    def status(self) -> dict:
        now = time.time()
        return {k: f"DOWN {int(v - now)}s" for k, v in self._down_until.items()}


_cb = _CircuitBreaker()


def get_symbol_stats(symbol: str, direction: str, days: int = 30) -> dict:
    try:
        from core_api.models import SessionLocal, TradeJournal
        db    = SessionLocal()
        since = datetime.utcnow() - timedelta(days=days)
        rows  = (db.query(TradeJournal)
                 .filter(TradeJournal.symbol    == symbol,
                         TradeJournal.direction  == direction,
                         TradeJournal.timestamp >= since)
                 .order_by(TradeJournal.timestamp.desc())
                 .limit(20)
                 .all())
        db.close()
    except Exception as e:
        log.debug("get_symbol_stats DB error: %s", e)
        return {"win_rate": 50, "total": 0, "confidence_mult": 1.0,
                "reason": "Chưa có dữ liệu lịch sử"}

    if len(rows) < 3:
        return {"win_rate": 50, "total": len(rows), "confidence_mult": 1.0,
                "reason": f"Chỉ có {len(rows)} giao dịch — chưa đủ mẫu (cần ≥3)"}

    wins    = [r for r in rows if (r.pnl_pct or 0) > 0]
    losses  = [r for r in rows if (r.pnl_pct or 0) <= 0]
    wr      = round(len(wins) / len(rows) * 100, 1)
    avg_pnl = round(sum(r.pnl_pct or 0 for r in rows) / len(rows), 2)
    recent  = [r.outcome or ("TP" if (r.pnl_pct or 0) > 0 else "SL") for r in rows[:5]]

    streak = 0
    streak_type = recent[0] if recent else "N/A"
    for o in recent:
        if o == streak_type:
            streak += 1
        else:
            break

    if   wr >= 75 and avg_pnl > 1.5:
        mult, reason = 1.10, f"WR {wr}% ({len(rows)} lệnh) avg+{avg_pnl}% → +10% confidence"
    elif wr >= 65:
        mult, reason = 1.05, f"WR {wr}% ({len(rows)} lệnh) → +5% confidence"
    elif wr >= 50:
        mult, reason = 1.00, f"WR {wr}% ({len(rows)} lệnh) → neutral"
    elif wr >= 40:
        mult, reason = 0.93, f"WR {wr}% ({len(rows)} lệnh) → -7% confidence"
    elif wr >= 30:
        mult, reason = 0.85, f"⚠️ WR {wr}% ({len(rows)} lệnh) → -15% confidence"
    else:
        mult, reason = 0.78, f"🚫 WR {wr}% ({len(rows)} lệnh) → -22% confidence (poor history)"

    if streak >= 3 and streak_type in ("SL", "LOSS"):
        mult   = round(mult * 0.90, 3)
        reason += f" | ⚠️ {streak} SL liên tiếp → thêm -10%"

    return {
        "win_rate":          wr,
        "total":             len(rows),
        "wins":              len(wins),
        "losses":            len(losses),
        "avg_pnl":           avg_pnl,
        "recent_outcomes":   recent,
        "confidence_mult":   round(mult, 3),
        "reason":            reason,
    }


def apply_statistical_overlay(symbol: str, direction: str,
                               raw_confidence: float) -> tuple[float, str]:
    stats = get_symbol_stats(symbol, direction)
    if stats["total"] < 3:
        return raw_confidence, "Chưa đủ dữ liệu lịch sử"

    adjusted = round(min(95, max(30, raw_confidence * stats["confidence_mult"])), 1)
    delta    = round(adjusted - raw_confidence, 1)
    sign     = "+" if delta >= 0 else ""
    explain  = (f"Statistical Overlay: WR={stats['win_rate']}% ({stats['total']} trades) "
                f"→ {sign}{delta}% ({raw_confidence}% → {adjusted}%)\n"
                f"  {stats['reason']}\n"
                f"  Recent: {' → '.join(stats['recent_outcomes'][:5])}")
    return adjusted, explain


def get_memory_for_ai(symbol: str) -> str:
    try:
        from core_api.main import SessionLocal, TradeJournal
        db = SessionLocal()
        lessons = db.query(TradeJournal).filter(
            TradeJournal.symbol == symbol,
            TradeJournal.pnl_pct < 0
        ).order_by(TradeJournal.timestamp.desc()).limit(3).all()
        db.close()
        if not lessons:
            return ""
        mem = "⚠️ CẢNH BÁO TỪ QUÁ KHỨ (CÁC LỆNH BỊ CẮT LỖ GẦN NHẤT):\n"
        for l in lessons:
            mem += f"- Đánh {l.direction}: {l.lesson}\n"
        return mem
    except Exception as e:
        log.warning(f"Lỗi lấy bộ nhớ AI: {e}")
        return ""


class LLMChain:

    def __init__(self):
        self.groq_key    = cfg.GROQ_API_KEY
        self.gemini_key  = cfg.GEMINI_API_KEY
        self.mistral_key = cfg.MISTRAL_API_KEY
        self.nvidia_key  = getattr(cfg, "NVIDIA_API_KEY", "")

    def _slot(self):
        m = datetime.now().minute
        if   m < 15: s = 0
        elif m < 30: s = 1
        elif m < 45: s = 2
        else:        s = 3
        names = {0: "Groq", 1: "Mistral", 2: "NVIDIA NIM", 3: "Rule-based"}
        log.info("Chiếu slot AI: %d → %s", m, names[s])
        return s

    def _prompt(self, data):
        fibo  = data.get("fibo", {})
        wy    = data.get("wyckoff", {})
        cvd   = data.get("cvd", {})
        vol   = data.get("volume_1h", {})
        ell   = data.get("elliott_4h", {})
        atype = data.get("asset_type", "CRYPTO")
        ctx   = {"CRYPTO": "crypto futures", "STOCK": "cổ phiếu Mỹ",
                 "GOLD":   "vàng NCCOGOLD2USD-USDT"}.get(atype, "")
        tfs   = " | ".join(tf + ":" + r["direction"][0] + "(" + str(r["score"]) + ")"
                           for tf, r in data["timeframes"].items())
        return ("Chuyên gia " + ctx + ". Phân tích ngắn (150 từ):\n"
                + data["symbol"] + " \$" + str(data["price"]) + " | "
                + data["final"] + " " + str(data["confidence"]) + "%\n"
                "TF: " + tfs + "\n"
                "Elliott:" + str(ell.get("wave_pattern","?")) + " (" + str(ell.get("current_wave","?")) + ")\n"
                "CVD:" + str(cvd.get("trend","?")) + " Vol:" + str(vol.get("vol_trend","?")) + "\n"
                "Fibo:" + str(fibo.get("trend","?")) + " Zone:" + str(fibo.get("zone","?")) + "\n"
                "Wyckoff:" + str(wy.get("phase","?")) + " " + str(wy.get("bias","?")) + "\n"
                "SL:\$" + str(data["plan"]["sl"]) + " TP1:\$" + str(data["plan"]["tp1"]) + "\n\n"
                "Format:\nKY_THUAT: ...\nHANH_DONG: [MUA/BAN/CHO]\nRUI_RO: ...")

    def _groq(self, prompt):
        if not self.groq_key:
            raise ValueError("No Groq key")
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": "Bearer " + self.groq_key,
                     "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 300, "temperature": 0.3},
            timeout=20)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    def _gemini(self, prompt):
        if not self.gemini_key:
            raise ValueError("No Gemini key")
        for model in ["gemini-2.0-flash-lite", "gemini-2.0-flash"]:
            try:
                r = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    + model + ":generateContent?key=" + self.gemini_key,
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"maxOutputTokens": 300, "temperature": 0.3}},
                    timeout=10)
                if r.status_code in (429, 404, 403):
                    time.sleep(0.5)
                    continue
                r.raise_for_status()
                return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception:
                continue
        raise RuntimeError("Gemini all fail")

    def _mistral(self, prompt):
        if not self.mistral_key:
            raise ValueError("No Mistral key")
        r = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": "Bearer " + self.mistral_key,
                     "Content-Type": "application/json"},
            json={"model": "mistral-small-latest",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 300, "temperature": 0.3},
            timeout=20)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    def _nvidia_nim(self, prompt, fast=False):
        if not self.nvidia_key:
            raise ValueError("No NVIDIA NIM key")
        models = (["meta/llama-3.1-8b-instruct", "meta/llama-3.3-70b-instruct"] if fast
                  else ["meta/llama-3.3-70b-instruct", "meta/llama-3.1-8b-instruct"])
        for model in models:
            try:
                r = requests.post(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    headers={"Authorization": "Bearer " + self.nvidia_key,
                             "Content-Type": "application/json"},
                    json={"model": model,
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens":  300,
                          "temperature": 0.3,
                          "top_p":       0.9,
                          "stream":      False},
                    timeout=25)
                if r.status_code == 429:
                    log.warning("NVIDIA NIM rate limit — thử model tiếp theo")
                    time.sleep(2)
                    continue
                r.raise_for_status()
                result = r.json()["choices"][0]["message"]["content"].strip()
                if result:
                    log.info("  NVIDIA NIM [%s] OK", model.split("/")[-1])
                    return result
            except Exception as e:
                log.warning("NVIDIA NIM [%s]: %s", model, e)
                continue
        raise RuntimeError("NVIDIA NIM: tất cả models thất bại")

    def _rule(self, data):
        sig   = data["final"]
        conf  = data["confidence"]
        price = data["price"]
        plan  = data["plan"]
        fibo  = data.get("fibo", {})
        wy    = data.get("wyckoff", {})
        cvd   = data.get("cvd", {})
        vol   = data.get("volume_1h", {})
        bo    = data.get("breakout", {})
        wh    = data.get("whale", {})
        tfs   = data.get("timeframes", {})
        lvls  = fibo.get("levels", {})
        atype = data.get("asset_type", "CRYPTO")
        fund  = data.get("funding", 0)

        tf1h   = tfs.get("1h", {})
        tf4h   = tfs.get("4h", {})
        tf15m  = tfs.get("15m", {})
        rsi_1h = tf1h.get("rsi", 50)
        rsi_4h = tf4h.get("rsi", 50)
        macd_1h = tf1h.get("macd", {}).get("cross", "NEUTRAL")
        ema_1h  = tf1h.get("ema", {})
        bb_1h   = tf1h.get("bb", {})
        stoch_1h = tf1h.get("stoch", {})

        tech_points = []
        if rsi_1h < 30:
            tech_points.append("RSI 1H=" + str(rsi_1h) + " — vùng oversold mạnh, áp lực mua tăng")
        elif rsi_1h < 45:
            tech_points.append("RSI 1H=" + str(rsi_1h) + " — momentum yếu, chưa xác nhận đảo chiều")
        elif rsi_1h > 70:
            tech_points.append("RSI 1H=" + str(rsi_1h) + " — vùng overbought, cẩn thận pullback")
        elif rsi_1h > 55:
            tech_points.append("RSI 1H=" + str(rsi_1h) + " — momentum bullish đang hình thành")
        else:
            tech_points.append("RSI 1H=" + str(rsi_1h) + " — trung tính, chờ xác nhận hướng")

        if abs(rsi_1h - rsi_4h) < 10:
            tech_points.append("RSI 4H=" + str(rsi_4h) + " — đồng thuận với 1H, tín hiệu đáng tin")
        elif rsi_1h < 50 and rsi_4h > 55:
            tech_points.append("RSI divergence: 1H bearish nhưng 4H vẫn bullish — cẩn thận")

        if macd_1h == "BULL_CROSS":
            hist = tf1h.get("macd", {}).get("hist", 0)
            tech_points.append("MACD 1H bullish cross" +
                                (" + histogram dương — đà tăng" if hist > 0 else " — chờ histogram xác nhận"))
        elif macd_1h == "BEAR_CROSS":
            tech_points.append("MACD 1H bearish cross — áp lực bán đang tăng")

        if ema_1h.get("bull"):
            tech_points.append("EMA 20>50>200: stack bullish hoàn chỉnh — uptrend trung hạn xác nhận")
        elif ema_1h.get("bear"):
            tech_points.append("EMA 20<50<200: stack bearish hoàn chỉnh — downtrend trung hạn xác nhận")

        bb_pct = bb_1h.get("pct", 50)
        if bb_1h.get("squeeze"):
            tech_points.append("BB Squeeze — tích lũy, sắp có breakout mạnh")
        elif bb_pct < 15:
            tech_points.append("Giá gần BB Lower (" + str(bb_pct) + "%) — oversold ngắn hạn")
        elif bb_pct > 85:
            tech_points.append("Giá gần BB Upper (" + str(bb_pct) + "%) — overbought ngắn hạn")

        fibo_wyc_points = []
        fibo_trend = fibo.get("trend", "?")
        fibo_zone  = fibo.get("zone", "BETWEEN")
        in_golden  = fibo.get("in_golden", False)

        if in_golden:
            fibo_wyc_points.append(
                "Giá trong Golden Zone 0.382-0.618 — " +
                ("vùng mua lý tưởng trong uptrend" if fibo_trend == "UPTREND"
                 else "vùng bán lý tưởng trong downtrend"))
        elif "NEAR_0.618" in fibo_zone:
            fibo_wyc_points.append(
                "Giá test 0.618 (\$" + str(lvls.get("0.618","?")) + ") — " +
                ("support quan trọng" if fibo_trend == "UPTREND" else "resistance mạnh"))

        wy_phase = wy.get("phase", "TRANSITION")
        wy_bias  = wy.get("bias", "NEUTRAL")
        wy_pos   = wy.get("pos_range", 50)
        wy_desc  = {
            "ACCUMULATION":    "Smart money đang gom hàng (Pos=" + str(wy_pos) + "%) — chuẩn bị tăng",
            "MARKUP":          "Markup phase — uptrend xác nhận",
            "RE-ACCUMULATION": "Tích lũy lại trước sóng kế tiếp",
            "DISTRIBUTION":    "⚠️ Smart money phân phối (Pos=" + str(wy_pos) + "%) — rủi ro đảo chiều",
            "MARKDOWN":        "Markdown phase — downtrend xác nhận",
        }.get(wy_phase, "Phase đang chuyển đổi")
        fibo_wyc_points.append("Wyckoff " + wy_phase + ": " + wy_desc)

        ell = data.get("elliott_4h", {})
        if ell.get("wave_pattern"):
            fibo_wyc_points.append(f"Elliott Wave: {ell.get('wave_pattern')} ({ell.get('current_wave')}) — {ell.get('description')}")

        smart_points = []
        cvd_trend = cvd.get("trend", "NEUTRAL")
        cvd_div   = cvd.get("divergence", False)
        cvd_msgs  = {"BULLISH": "CVD BULLISH — áp lực mua thực sự đang tăng mạnh",
                     "BEARISH": "CVD BEARISH — áp lực bán chiếm ưu thế",
                     "BULLISH_DIV": "CVD Bullish Divergence — giá giảm nhưng lực mua tích lũy",
                     "BEARISH_DIV": "⚠️ CVD Bearish Divergence — giá tăng nhưng lực mua yếu dần"}
        if cvd_trend in cvd_msgs:
            smart_points.append(cvd_msgs[cvd_trend])

        obv_trend  = vol.get("obv_trend", "NEUTRAL")
        vol_trend  = vol.get("vol_trend", "NORMAL")
        vol_ratio  = vol.get("vol_ratio", 1.0)
        pressure   = vol.get("pressure", "NEUTRAL")
        buy_pct    = vol.get("buy_pct", 50)
        poc_price  = vol.get("poc", price)

        if vol_trend == "SURGE":
            smart_points.append("Volume SURGE " + str(vol_ratio) + "x — xác nhận breakout")
        elif vol_trend == "DRY":
            smart_points.append("Volume cạn kiệt — thiếu conviction, tránh vào lệnh lớn")

        if pressure == "STRONG_BUY":
            smart_points.append("Buy Pressure " + str(buy_pct) + "% — người mua chiếm ưu thế")
        elif pressure == "STRONG_SELL":
            smart_points.append("Sell Pressure " + str(100-buy_pct) + "% — người bán chiếm ưu thế")

        smart_points.append("POC \$" + str(poc_price) + " — giá đang " + ("trên" if price > poc_price else "dưới") + " POC")

        if bo.get("type","NONE") != "NONE":
            smart_points.append(f"🚨 BREAKOUT: {bo.get('desc','')}")

        lines = [
            "[RULE_BASE_ENGINE]",
            "KY_THUAT:",
            "  - " + "\n  - ".join(tech_points[:3]),
            "FIBONACCI_WYCKOFF:",
            "  - " + "\n  - ".join(fibo_wyc_points[:2]),
            "ON_CHAIN_DERIVATIVES:",
            "  - " + "\n  - ".join(smart_points[:3]),
            "VERDICT: " + sig + " | Conf: " + str(conf) + "%",
            "PLAN: Entry=" + str(price) + " SL=" + str(plan["sl"]) + " TP=" + str(plan["tp1"])
        ]
        return "\n".join(lines)

    def analyze(self, data):
        prompt = self._prompt(data)
        slot = self._slot()

        rotation = {
            0: [("Groq",    lambda p: self._groq(p)),
                ("NVIDIA",  lambda p: self._nvidia_nim(p, fast=True)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Gemini",  lambda p: self._gemini(p))],
            1: [("Mistral", lambda p: self._mistral(p)),
                ("Groq",    lambda p: self._groq(p)),
                ("NVIDIA",  lambda p: self._nvidia_nim(p, fast=True)),
                ("Gemini",  lambda p: self._gemini(p))],
            2: [("NVIDIA",  lambda p: self._nvidia_nim(p, fast=True)),
                ("Groq",    lambda p: self._groq(p)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Gemini",  lambda p: self._gemini(p))],
            3: [("NVIDIA",  lambda p: self._nvidia_nim(p, fast=True)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Groq",    lambda p: self._groq(p)),
                ("Gemini",  lambda p: self._gemini(p))],
        }

        for name, fn in rotation.get(slot, rotation[0]):
            if not _cb.is_up(name):
                log.info("  ⏭️  [%s] circuit open — skip", name)
                continue
            try:
                result = fn(prompt)
                if result and len(result.strip()) > 30:
                    _cb.ok(name)
                    log.info("  Agent [%s] OK", name)
                    return result.strip(), name
                _cb.fail(name)
            except Exception as e:
                log.warning("  Agent [%s] lỗi: %s", name, e)
                _cb.fail(name)

        log.warning("⚠️ Tất cả LLM lỗi hoặc circuit open, fallback sang Rule Base Engine")
        return self._rule(data), "Rule-based"

    def run(self, data):
        log.info("🤖 Multi-Agent Pipeline bắt đầu...")

        memory = data.get("ai_memory", get_memory_for_ai(data["symbol"]))
        def inject(prompt):
            return (prompt + "\n\n--- KINH NGHIEM QUA KHU ---\n" + memory + "\n---")  if memory else prompt

        S1  = ["TECHNICAL_ANALYST","ONCHAIN_ANALYST","MACRO_ANALYST","MOMENTUM_ANALYST"]
        S2  = ["BULL_RESEARCHER","BEAR_RESEARCHER","RESEARCH_MANAGER"]
        S34 = ["TRADER_AGENT","RISK_AGGRESSIVE","RISK_CONSERVATIVE","RISK_NEUTRAL"]
        S5  = ["FINAL_VERDICT"]

        log.info("  Stage 1: 4 Domain Analysts...")
        s1_raw = self._call(inject(self._p_stage1(data)), fast=True)
        s1     = self._parse(s1_raw, S1)
        time.sleep(1)

        log.info("  Stage 2: Bull/Bear/Manager...")
        s2_raw = self._call(inject(self._p_stage2(data, s1_raw)), fast=True)
        s2     = self._parse(s2_raw, S2)
        time.sleep(1)

        log.info("  Stage 3-4: Trader + Risk Debate...")
        s34_raw = self._call(inject(self._p_stage34(data, s2_raw)), fast=True)
        s34     = self._parse(s34_raw, S34)
        time.sleep(1)

        log.info("  Stage 5: CIO Final Verdict...")
        s5_raw = self._call(inject(self._p_stage5(data, s1_raw, s2_raw, s34_raw)), fast=False)
        s5     = self._parse(s5_raw, S5)

        log.info("✅ Pipeline hoàn thành!")

        signal_direction = "LONG"
        if "SIGNAL: SHORT" in s5_raw.upper():
            signal_direction = "SHORT"
        elif "SIGNAL: WAIT" in s5_raw.upper():
            signal_direction = "WAIT"

        stat_adj_conf = None
        stat_explain  = ""
        if signal_direction != "WAIT":
            raw_conf = float(data.get("confidence", 70))
            stat_adj_conf, stat_explain = apply_statistical_overlay(
                data["symbol"], signal_direction, raw_conf)

        return {"stage1": s1, "stage2": s2,
                "stage3": {"TRADER_AGENT": s34.get("TRADER_AGENT", "N/A")},
                "stage4": {k: s34.get(k, "N/A") for k in S34[1:]},
                "stage5": s5,
                "stat_confidence": stat_adj_conf,
                "stat_explanation": stat_explain,
                "stat_direction":   signal_direction}

    def _call(self, prompt, fast=False):
        slot = self._slot()

        rotation = {
            0: [("Groq",    lambda p: self._groq(p)),
                ("NVIDIA",  lambda p: self._nvidia_nim(p, fast=fast)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Gemini",  lambda p: self._gemini(p))],
            1: [("Mistral", lambda p: self._mistral(p)),
                ("Groq",    lambda p: self._groq(p)),
                ("NVIDIA",  lambda p: self._nvidia_nim(p, fast=fast)),
                ("Gemini",  lambda p: self._gemini(p))],
            2: [("NVIDIA",  lambda p: self._nvidia_nim(p, fast=fast)),
                ("Groq",    lambda p: self._groq(p)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Gemini",  lambda p: self._gemini(p))],
            3: [("NVIDIA",  lambda p: self._nvidia_nim(p, fast=fast)),
                ("Mistral", lambda p: self._mistral(p)),
                ("Groq",    lambda p: self._groq(p)),
                ("Gemini",  lambda p: self._gemini(p))],
        }

        for name, fn in rotation.get(slot, rotation[0]):
            if not _cb.is_up(name):
                log.info("  ⏭️  [%s] circuit open — skip", name)
                continue
            try:
                result = fn(prompt)
                if result and len(result.strip()) > 30:
                    _cb.ok(name)
                    log.info("  Agent [%s] OK", name)
                    return result.strip()
                _cb.fail(name)
            except Exception as e:
                log.warning("  Agent [%s] lỗi: %s", name, e)
                _cb.fail(name)
        return ""

    def _summary(self, data):
        fibo = data.get("fibo", {})
        wy   = data.get("wyckoff", {})
        cvd  = data.get("cvd", {})
        vol  = data.get("volume_1h", {})
        bo   = data.get("breakout", {})
        lvls = fibo.get("levels", {})
        
        tfs  = "\n".join(
            "  " + tf + ": " + r["direction"] +
            " score=" + str(r["score"]) +
            " rsi="   + str(r["rsi"]) +
            " macd="  + r["macd"]["cross"]
            for tf, r in data["timeframes"].items())
        return (
            "Asset  : " + data["symbol"] + " (" + data.get("asset_type","") + ")\n"
            "Gia    : \$" + str(round(data["price"], 2)) + "\n"
            "Signal : " + data["final"] + " " + str(data["confidence"]) + "%\n"
            "TF:\n" + tfs + "\n"
            "CVD=" + str(cvd.get("trend","?")) +
            " OBV=" + str(vol.get("obv_trend","?")) +
            " Vol=" + str(vol.get("vol_trend","?")) + "x" + str(vol.get("vol_ratio",1)) + "\n"
            "Pressure=" + str(vol.get("pressure","?")) + "(" + str(vol.get("buy_pct",50)) + "%buy)"
            " VWAP=\$" + str(vol.get("vwap",0)) + "\n"
            "Fibo=" + str(fibo.get("trend","?")) +
            " Zone=" + str(fibo.get("zone","?")) + "\n"
            "Wyckoff=" + str(wy.get("phase","?")) + " " + str(wy.get("bias","?")) + "\n"
            "Breakout=" + str(bo.get("type","NONE"))
        )

    def _p_stage1(self, data):
        atype = data.get("asset_type","CRYPTO")
        ctx = {"CRYPTO": "crypto. Xem on-chain, whale, derivatives",
               "STOCK":  "co phieu My. Xem earnings, Fed, sector",
               "GOLD":   "vang NCCOGOLD2USD-USDT. Xem DXY, lai suat Fed, CPI",
               }.get(atype, "tai san tai chinh")
        return (
            "Ban la 4 chuyen gia phan tich " + ctx + ".\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n"
            "Viet bang tieng Viet, ngan gon (toi da 80 tu/agent), dua vao so lieu cu the.\n\n"
            "DU LIEU:\n" + self._summary(data) + "\n\n"
            "[TECHNICAL_ANALYST]\nPhan tich RSI/MACD/EMA/BB/Fibonacci/Wyckoff.\n"
            "Neu 2 tin hieu manh nhat. Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[ONCHAIN_ANALYST]\nPhan tich CVD/OBV/Buy Pressure/OI/Funding/Whale.\n"
            "Nhu cau mua/ban thuc su? Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MACRO_ANALYST]\nPhan tich yeu to vi mo anh huong den " + data["symbol"] + ".\n"
            "1-2 catalyst hoac rui ro macro. Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MOMENTUM_ANALYST]\nPhan tich VWAP, POC, breakout level.\n"
            "Momentum dang tang hay giam? Bias: BULLISH/BEARISH/NEUTRAL"
        )

    def _p_stage2(self, data, s1):
        return (
            "Ban la 2 nha nghien cuu va 1 quan ly.\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n\n"
            "KET QUA STAGE 1:\n" + s1[:800] + "\n\n"
            "GIA: " + data["symbol"] + " \$" + str(round(data["price"],2)) +
            " | SL=\$" + str(data["plan"]["sl"]) + " TP1=\$" + str(data["plan"]["tp1"]) + "\n\n"
            "[BULL_RESEARCHER]\n3 ly do MUA manh nhat. Muc tieu gia?\n\n"
            "[BEAR_RESEARCHER]\n3 rui ro lon nhat neu MUA. Muc tieu gia?\n\n"
            "[RESEARCH_MANAGER]\nBull vs Bear — ai thuyet phuc hon?\n"
            "Verdict: BULLISH/BEARISH/NEUTRAL. Conviction: HIGH/MEDIUM/LOW."
        )

    def _p_stage34(self, data, s2):
        mgr_idx  = s2.find("[RESEARCH_MANAGER]")
        mgr_text = s2[mgr_idx:mgr_idx+300] if mgr_idx >= 0 else s2[-300:]
        return (
            "Ban la 1 Trader va 3 quan ly rui ro.\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n\n"
            "RESEARCH VERDICT:\n" + mgr_text + "\n\n"
            "GIA: " + data["symbol"] + " \$" + str(round(data["price"],2)) +
            " | SL=\$" + str(data["plan"]["sl"]) + " TP1=\$" + str(data["plan"]["tp1"]) + "\n\n"
            "[TRADER_AGENT]\nAction: LONG/SHORT/WAIT\nEntry/SL/TP1/Size/RR (cu the)\nLy do: (2 cau)\n\n"
            "[RISK_AGGRESSIVE]\nDieu chinh de toi da hoa loi nhuan.\nVerdict: EXECUTE/WAIT\n\n"
            "[RISK_CONSERVATIVE]\nRui ro chua tinh den? Can them dieu kien gi?\nVerdict: EXECUTE/WAIT\n\n"
            "[RISK_NEUTRAL]\nKe hoach can bang: Entry/SL/TP/Size\nVerdict: EXECUTE/WAIT"
        )

    def _p_stage5(self, data, s1, s2, s34):
        def count_bias(text):
            bull, bear = 0, 0
            for line in text.upper().split("\n"):
                if any(k in line for k in ("BIAS:", "VERDICT:", "SIGNAL:")):
                    if any(w in line for w in ("BULLISH","LONG","EXECUTE")): bull += 1
                    elif any(w in line for w in ("BEARISH","SHORT","WAIT")): bear += 1
            return bull, bear

        b1,  r1  = count_bias(s1)
        b2,  r2  = count_bias(s2)
        b34, r34 = count_bias(s34)
        total_bull = b1 + b2 + b34
        total_bear = r1 + r2 + r34
        total_vote = total_bull + total_bear
        bull_pct   = round(total_bull / total_vote * 100) if total_vote > 0 else 50

        if   bull_pct >= 65: consensus_signal, consensus_level = "LONG",  "STRONG" if bull_pct >= 75 else "MODERATE"
        elif bull_pct <= 35: consensus_signal, consensus_level = "SHORT", "STRONG" if bull_pct <= 25 else "MODERATE"
        else:                consensus_signal, consensus_level = "WAIT",  "SPLIT"

        mgr_idx    = s2.find("[RESEARCH_MANAGER]")
        mgr_text   = s2[mgr_idx:mgr_idx+300] if mgr_idx >= 0 else s2[-200:]
        trader_idx = s34.find("[TRADER_AGENT]")
        trader_txt = s34[trader_idx:trader_idx+200] if trader_idx >= 0 else ""
        neu_idx    = s34.find("[RISK_NEUTRAL]")
        neu_txt    = s34[neu_idx:neu_idx+200] if neu_idx >= 0 else ""
        exec_count = s34.upper().count("VERDICT: EXECUTE")
        wait_count = s34.upper().count("VERDICT: WAIT")

        price, sl, tp1, tp2, rr = (data["price"], data["plan"]["sl"],
                                    data["plan"]["tp1"], data["plan"]["tp2"],
                                    data.get("rr_ratio", 2.0))
        return (
            "Ban la CIO — nguoi ra quyet dinh cuoi cung.\n"
            "YEU CAU: Bat dau bang [FINAL_VERDICT] trong ngoac vuong.\n\n"
            "=== PHIEU BAU TU 12 AGENTS ===\n"
            "Stage 1: Bull=" + str(b1) + " Bear=" + str(r1) + "\n"
            "Stage 2: Bull=" + str(b2) + " Bear=" + str(r2) + "\n"
            "Stage 3-4: Bull=" + str(b34) + " Bear=" + str(r34) + "\n"
            "TONG: Bull=" + str(total_bull) + " Bear=" + str(total_bear) +
            " (" + str(bull_pct) + "% Bull)\n"
            "CONSENSUS: " + consensus_signal + " [" + consensus_level + "]\n\n"
            "Research Manager: " + mgr_text[:200] + "\n"
            "Trader: " + trader_txt[:150] + "\n"
            "Risk Neutral: " + neu_txt[:150] + "\n"
            "Risk Debaters: " + str(exec_count) + " EXECUTE / " + str(wait_count) + " WAIT\n\n"
            + data["symbol"] + " \$" + str(round(price, 2)) +
            " | SL=\$" + str(sl) + " TP1=\$" + str(tp1) + " TP2=\$" + str(tp2) + " R:R=1:" + str(rr) + "\n\n"
            "NHIEM VU: Consensus la " + consensus_signal + " (" + str(bull_pct) + "% Bull).\n\n"
            "[FINAL_VERDICT]\n"
            "Signal: " + consensus_signal + "\nConfidence: ...%\n"
            "Consensus: " + consensus_level + " (" + str(bull_pct) + "% Bull)\n"
            "Entry: \$" + str(round(price, 2)) + "\nSL: \$" + str(sl) +
            "\nTP1: \$" + str(tp1) + "\nTP2: \$" + str(tp2) + "\nSize: 2% von\n"
            "Tom_tat: (quyet dinh + ly do chinh + canh bao)\n"
            "Kich_ban_xau: (dieu kien nao se lam signal nay sai?)"
        )

    def _parse(self, text, tags):
        if not text:
            return {t: "Chưa có phân tích" for t in tags}
        norm = text
        for tag in tags:
            esc = re.escape(tag)
            for pat in [r"\*{1,3}" + esc + r"\*{1,3}",
                        r"#{1,4}\s*" + esc,
                        r"\d+[.)]\s*" + esc,
                        r"\[" + esc + r"\]"]:
                norm = re.sub(pat, "[" + tag + "]", norm, flags=re.I)
            norm = re.sub(r"(\[" + re.escape(tag) + r"\]\s*){2,}",
                          "[" + tag + "]\n", norm, flags=re.I)
        result = {}
        for tag in tags:
            marker = "[" + tag + "]"
            start  = norm.find(marker)
            if start == -1:
                result[tag] = "Không tìm thấy phân tích"
                continue
            end = len(norm)
            for other in tags:
                if other == tag: continue
                p = norm.find("[" + other + "]", start + 1)
                if 0 < p < end: end = p
            block = norm[start + len(marker):end].strip()
            result[tag] = block if block else "Nội dung rỗng"
        return result


class MultiAgentPipeline:

    def __init__(self, llm_chain):
        self.llm = llm_chain

    def _call(self, prompt, fast=False):
        return self.llm._call(prompt, fast=fast)

    def run(self, data):
        log.info("🤖 Multi-Agent Pipeline bắt đầu...")

        memory = data.get("ai_memory", get_memory_for_ai(data["symbol"]))
        def inject(prompt):
            return (prompt + "\n\n--- KINH NGHIEM QUA KHU ---\n" + memory + "\n---")  if memory else prompt

        # Inject L2 Orderbook and Liquidity Sweep into prompts for Stage 5
        sweep = data.get("liquidity_sweep", {})
        ob    = data.get("orderbook", {})
        
        extra_context = ""
        if sweep.get("detected"):
            extra_context += f"- 🔥 BẪY THANH KHOẢN ({sweep.get('type')}): Đã quét râu tại mức giá {sweep.get('price')} kèm Volume lớn. Hãy phân tích đây là hành động săn thanh khoản của cá mập trước khi đảo chiều.\n"
            
        if ob.get("detected"):
            extra_context += f"- 🧱 SỔ LỆNH L2 (Order Book): Tường mua cứng (hỗ trợ) tại {ob.get('support_wall')}, Tường bán cứng (kháng cự) tại {ob.get('resist_wall')}. Độ lệch Imbalance là {ob.get('imbalance')} (Âm = phe Bán áp đảo, Dương = phe Mua áp đảo).\n"

        S1  = ["TECHNICAL_ANALYST","ONCHAIN_ANALYST","MACRO_ANALYST","MOMENTUM_ANALYST"]
        S2  = ["BULL_RESEARCHER","BEAR_RESEARCHER","RESEARCH_MANAGER"]
        S34 = ["TRADER_AGENT","RISK_AGGRESSIVE","RISK_CONSERVATIVE","RISK_NEUTRAL"]
        S5  = ["FINAL_VERDICT"]

        log.info("  Stage 1: 4 Domain Analysts...")
        s1_raw = self._call(inject(self._p_stage1(data)), fast=True)
        s1     = self._parse(s1_raw, S1)
        time.sleep(1)

        log.info("  Stage 2: Bull/Bear/Manager...")
        s2_raw = self._call(inject(self._p_stage2(data, s1_raw)), fast=True)
        s2     = self._parse(s2_raw, S2)
        time.sleep(1)

        log.info("  Stage 3-4: Trader + Risk Debate...")
        s34_raw = self._call(inject(self._p_stage34(data, s2_raw)), fast=True)
        s34     = self._parse(s34_raw, S34)
        time.sleep(1)

        log.info("  Stage 5: CIO Final Verdict...")
        stage5_prompt = self._p_stage5(data, s1_raw, s2_raw, s34_raw)
        if extra_context:
            stage5_prompt += "\n\n=== CHÚ Ý DỮ LIỆU SỔ LỆNH & THANH KHOẢN ===\n" + extra_context
        
        s5_raw = self._call(inject(stage5_prompt), fast=False)
        s5     = self._parse(s5_raw, S5)

        signal_direction = "LONG"
        if "SIGNAL: SHORT" in s5_raw.upper():
            signal_direction = "SHORT"
        elif "SIGNAL: WAIT" in s5_raw.upper():
            signal_direction = "WAIT"

        stat_adj_conf = None
        stat_explain  = ""
        if signal_direction != "WAIT":
            raw_conf = float(data.get("confidence", 70))
            stat_adj_conf, stat_explain = apply_statistical_overlay(
                data["symbol"], signal_direction, raw_conf)

        return {"stage1": s1, "stage2": s2,
                "stage3": {"TRADER_AGENT": s34.get("TRADER_AGENT", "N/A")},
                "stage4": {k: s34.get(k, "N/A") for k in S34[1:]},
                "stage5": s5,
                "stat_confidence": stat_adj_conf,
                "stat_explanation": stat_explain,
                "stat_direction":   signal_direction}

    def _summary(self, data):
        fibo = data.get("fibo", {})
        wy   = data.get("wyckoff", {})
        cvd  = data.get("cvd", {})
        vol  = data.get("volume_1h", {})
        bo   = data.get("breakout", {})
        lvls = fibo.get("levels", {})
        tfs  = "\n".join(
            "  " + tf + ": " + r["direction"] +
            " score=" + str(r["score"]) +
            " rsi="   + str(r["rsi"]) +
            " macd="  + r["macd"]["cross"]
            for tf, r in data["timeframes"].items())
        return (
            "Asset  : " + data["symbol"] + " (" + data.get("asset_type","") + ")\n"
            "Gia    : \$" + str(round(data["price"], 2)) + "\n"
            "Signal : " + data["final"] + " " + str(data["confidence"]) + "%\n"
            "TF:\n" + tfs + "\n"
            "CVD=" + str(cvd.get("trend","?")) +
            " OBV=" + str(vol.get("obv_trend","?")) +
            " Vol=" + str(vol.get("vol_trend","?")) + "x" + str(vol.get("vol_ratio",1)) + "\n"
            "Pressure=" + str(vol.get("pressure","?")) + "(" + str(vol.get("buy_pct",50)) + "%buy)"
            " VWAP=\$" + str(vol.get("vwap",0)) + "(" + str(vol.get("vwap_signal","?")) + ")\n"
            "Fibo=" + str(fibo.get("trend","?")) +
            " Zone=" + str(fibo.get("zone","?")) + "\n"
            "Wyckoff=" + str(wy.get("phase","?")) + " " + str(wy.get("bias","?")) + "\n"
            "Breakout=" + str(bo.get("type","NONE"))
        )

    def _p_stage1(self, data):
        atype = data.get("asset_type","CRYPTO")
        ctx = {"CRYPTO": "crypto. Xem on-chain, whale, derivatives",
               "STOCK":  "co phieu My. Xem earnings, Fed, sector",
               "GOLD":   "vang NCCOGOLD2USD-USDT. Xem DXY, lai suat Fed, CPI",
               }.get(atype, "tai san tai chinh")
        return (
            "Ban la 4 chuyen gia phan tich " + ctx + ".\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n"
            "Viet bang tieng Viet, ngan gon (toi da 80 tu/agent), dua vao so lieu cu the.\n\n"
            "DU LIEU:\n" + self._summary(data) + "\n\n"
            "[TECHNICAL_ANALYST]\nPhan tich RSI/MACD/EMA/BB/Fibonacci/Wyckoff.\n"
            "Neu 2 tin hieu manh nhat. Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[ONCHAIN_ANALYST]\nPhan tich CVD/OBV/Buy Pressure/OI/Funding/Whale.\n"
            "Nhu cau mua/ban thuc su? Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MACRO_ANALYST]\nPhan tich yeu to vi mo anh huong den " + data["symbol"] + ".\n"
            "1-2 catalyst hoac rui ro macro. Bias: BULLISH/BEARISH/NEUTRAL\n\n"
            "[MOMENTUM_ANALYST]\nPhan tich da tang/giam, VWAP, POC, breakout level.\n"
            "Momentum dang tang hay giam? Bias: BULLISH/BEARISH/NEUTRAL"
        )

    def _p_stage2(self, data, s1):
        return (
            "Ban la 2 nha nghien cuu va 1 quan ly.\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n\n"
            "KET QUA STAGE 1:\n" + s1[:800] + "\n\n"
            "GIA: " + data["symbol"] + " \$" + str(round(data["price"],2)) +
            " | SL=\$" + str(data["plan"]["sl"]) + " TP1=\$" + str(data["plan"]["tp1"]) + "\n\n"
            "[BULL_RESEARCHER]\n3 ly do MUA manh nhat. Muc tieu gia?\n\n"
            "[BEAR_RESEARCHER]\n3 rui ro lon nhat neu MUA. Muc tieu gia?\n\n"
            "[RESEARCH_MANAGER]\nBull vs Bear — ai thuyet phuc hon?\n"
            "Verdict: BULLISH/BEARISH/NEUTRAL. Conviction: HIGH/MEDIUM/LOW. Ly do: (1 cau)"
        )

    def _p_stage34(self, data, s2):
        mgr_idx  = s2.find("[RESEARCH_MANAGER]")
        mgr_text = s2[mgr_idx:mgr_idx+300] if mgr_idx >= 0 else s2[-300:]
        return (
            "Ban la 1 Trader va 3 quan ly rui ro.\n"
            "QUY TAC: Moi agent bat dau bang [TAG] trong ngoac vuong.\n\n"
            "RESEARCH VERDICT:\n" + mgr_text + "\n\n"
            "GIA: " + data["symbol"] + " \$" + str(round(data["price"],2)) +
            " | SL=\$" + str(data["plan"]["sl"]) + " TP1=\$" + str(data["plan"]["tp1"]) + "\n\n"
            "[TRADER_AGENT]\nAction: LONG/SHORT/WAIT\nEntry/SL/TP1/Size/RR (cu the)\nLy do: (2 cau)\n\n"
            "[RISK_AGGRESSIVE]\nDieu chinh de toi da hoa loi nhuan.\nVerdict: EXECUTE/WAIT\n\n"
            "[RISK_CONSERVATIVE]\nRui ro chua tinh den? Can them dieu kien gi?\nVerdict: EXECUTE/WAIT\n\n"
            "[RISK_NEUTRAL]\nKe hoach can bang: Entry/SL/TP/Size\nVerdict: EXECUTE/WAIT"
        )

    def _p_stage5(self, data, s1, s2, s34):
        def count_bias(text):
            bull, bear = 0, 0
            for line in text.upper().split("\n"):
                if any(k in line for k in ("BIAS:", "VERDICT:", "SIGNAL:")):
                    if any(w in line for w in ("BULLISH","LONG","EXECUTE")): bull += 1
                    elif any(w in line for w in ("BEARISH","SHORT","WAIT")): bear += 1
            return bull, bear

        b1,  r1  = count_bias(s1)
        b2,  r2  = count_bias(s2)
        b34, r34 = count_bias(s34)
        total_bull = b1 + b2 + b34
        total_bear = r1 + r2 + r34
        total_vote = total_bull + total_bear
        bull_pct   = round(total_bull / total_vote * 100) if total_vote > 0 else 50

        if   bull_pct >= 65: consensus_signal, consensus_level = "LONG",  "STRONG" if bull_pct >= 75 else "MODERATE"
        elif bull_pct <= 35: consensus_signal, consensus_level = "SHORT", "STRONG" if bull_pct <= 25 else "MODERATE"
        else:                consensus_signal, consensus_level = "WAIT",  "SPLIT"

        mgr_idx    = s2.find("[RESEARCH_MANAGER]")
        mgr_text   = s2[mgr_idx:mgr_idx+300] if mgr_idx >= 0 else s2[-200:]
        trader_idx = s34.find("[TRADER_AGENT]")
        trader_txt = s34[trader_idx:trader_idx+200] if trader_idx >= 0 else ""
        neu_idx    = s34.find("[RISK_NEUTRAL]")
        neu_txt    = s34[neu_idx:neu_idx+200] if neu_idx >= 0 else ""
        exec_count = s34.upper().count("VERDICT: EXECUTE")
        wait_count = s34.upper().count("VERDICT: WAIT")

        price, sl, tp1, tp2, rr = (data["price"], data["plan"]["sl"],
                                    data["plan"]["tp1"], data["plan"]["tp2"],
                                    data.get("rr_ratio", 2.0))
        return (
            "Ban la CIO — nguoi ra quyet dinh cuoi cung.\n"
            "YEU CAU: Bat dau bang [FINAL_VERDICT] trong ngoac vuong.\n\n"
            "=== PHIEU BAU TU 12 AGENTS ===\n"
            "Stage 1: Bull=" + str(b1) + " Bear=" + str(r1) + "\n"
            "Stage 2: Bull=" + str(b2) + " Bear=" + str(r2) + "\n"
            "Stage 3-4: Bull=" + str(b34) + " Bear=" + str(r34) + "\n"
            "TONG: Bull=" + str(total_bull) + " Bear=" + str(total_bear) +
            " (" + str(bull_pct) + "% Bull)\n"
            "CONSENSUS: " + consensus_signal + " [" + consensus_level + "]\n\n"
            "Research Manager: " + mgr_text[:200] + "\n"
            "Trader: " + trader_txt[:150] + "\n"
            "Risk Neutral: " + neu_txt[:150] + "\n"
            "Risk Debaters: " + str(exec_count) + " EXECUTE / " + str(wait_count) + " WAIT\n\n"
            + data["symbol"] + " \$" + str(round(price, 2)) +
            " | SL=\$" + str(sl) + " TP1=\$" + str(tp1) + " TP2=\$" + str(tp2) + " R:R=1:" + str(rr) + "\n\n"
            "NHIEM VU: Consensus la " + consensus_signal + " (" + str(bull_pct) + "% Bull).\n"
            "QUAN TRONG: Signal va Tom_tat PHAI NHAT QUAN nhau.\n\n"
            "[FINAL_VERDICT]\n"
            "Signal: " + consensus_signal + "\nConfidence: ...%\n"
            "Consensus: " + consensus_level + " (" + str(bull_pct) + "% Bull)\n"
            "Entry: \$" + str(round(price, 2)) + "\nSL: \$" + str(sl) +
            "\nTP1: \$" + str(tp1) + "\nTP2: \$" + str(tp2) + "\nSize: 2% von\n"
            "Tom_tat: (quyet dinh + ly do chinh + canh bao)\n"
            "Kich_ban_xau: (dieu kien nao se lam signal nay sai?)"
        )

    def _parse(self, text, tags):
        if not text:
            return {t: "Chưa có phân tích" for t in tags}
        norm = text
        for tag in tags:
            esc = re.escape(tag)
            for pat in [r"\*{1,3}" + esc + r"\*{1,3}",
                        r"#{1,4}\s*" + esc,
                        r"\d+[.)]\s*" + esc,
                        r"\[" + esc + r"\]"]:
                norm = re.sub(pat, "[" + tag + "]", norm, flags=re.I)
            norm = re.sub(r"(\[" + re.escape(tag) + r"\]\s*){2,}",
                          "[" + tag + "]\n", norm, flags=re.I)
        result = {}
        for tag in tags:
            marker = "[" + tag + "]"
            start  = norm.find(marker)
            if start == -1:
                result[tag] = "Không tìm thấy phân tích"
                continue
            end = len(norm)
            for other in tags:
                if other == tag: continue
                p = norm.find("[" + other + "]", start + 1)
                if 0 < p < end: end = p
            block = norm[start + len(marker):end].strip()
            result[tag] = block if block else "Nội dung rỗng"
        return result

    @staticmethod
    def format_telegram(result, symbol):
        s1 = result["stage1"]
        s2 = result["stage2"]
        s3 = result["stage3"]
        s4 = result["stage4"]
        s5 = result["stage5"]

        def cut(text, n=350):
            t = str(text).strip()
            if len(t) <= n: return t
            truncated = t[:n]
            for sep in [". ","! ","? ","\n"]:
                pos = truncated.rfind(sep)
                if pos > int(n * 0.6):
                    return truncated[:pos + len(sep)].rstrip() + " [...]"
            pos = truncated.rfind(" ")
            return (truncated[:pos] if pos > int(n * 0.7) else truncated) + "[...]"

        def bias_icon(text):
            u = str(text).upper()
            if "BULLISH" in u: return "🟢"
            if "BEARISH" in u: return "🔴"
            return "🟡"

        def exec_icon(text):
            u = str(text).upper()
            if "EXECUTE" in u: return "✅"
            if "WAIT"    in u: return "⏳"
            return "❓"

        ta  = s1.get("TECHNICAL_ANALYST",  "N/A")
        oa  = s1.get("ONCHAIN_ANALYST",    "N/A")
        ma  = s1.get("MACRO_ANALYST",      "N/A")
        mom = s1.get("MOMENTUM_ANALYST",   "N/A")
        bull = s2.get("BULL_RESEARCHER",   "N/A")
        bear = s2.get("BEAR_RESEARCHER",   "N/A")
        mgr  = s2.get("RESEARCH_MANAGER",  "N/A")
        trd  = s3.get("TRADER_AGENT",      "N/A")
        agg  = s4.get("RISK_AGGRESSIVE",   "N/A")
        con  = s4.get("RISK_CONSERVATIVE", "N/A")
        neu  = s4.get("RISK_NEUTRAL",      "N/A")
        fin  = s5.get("FINAL_VERDICT",     "N/A")

        sig_icon = "⏳"
        fu = fin.upper()
        if "SIGNAL: LONG"  in fu: sig_icon = "🚀"
        if "SIGNAL: SHORT" in fu: sig_icon = "📉"

        msg1 = ("🔬 <b>STAGE 1 — 4 CHUYÊN GIA</b> | " + symbol + "\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n\n"
                "📊 <b>Kỹ Thuật</b> " + bias_icon(ta) + "\n" + cut(ta, 450) + "\n\n"
                "🔗 <b>On-Chain/Derivatives</b> " + bias_icon(oa) + "\n" + cut(oa, 450) + "\n\n"
                "🌍 <b>Macro/Fundamental</b> " + bias_icon(ma) + "\n" + cut(ma, 450) + "\n\n"
                "⚡ <b>Momentum/Price Action</b> " + bias_icon(mom) + "\n" + cut(mom, 450))

        msg2 = ("⚔️ <b>STAGE 2 — BULL vs BEAR</b> | " + symbol + "\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n\n"
                "🐂 <b>Bull Researcher</b>\n" + cut(bull, 550) + "\n\n"
                "🐻 <b>Bear Researcher</b>\n" + cut(bear, 550) + "\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n"
                "👔 <b>Research Manager Verdict</b>\n" + cut(mgr, 500))

        msg3 = ("💹 <b>STAGE 3-4 — TRADER + RISK</b> | " + symbol + "\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n"
                "💼 <b>Trader Agent</b>\n" + cut(trd, 550) + "\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n"
                "🔴 <b>Aggressive</b> " + exec_icon(agg) + "\n" + cut(agg, 280) + "\n\n"
                "🔵 <b>Conservative</b> " + exec_icon(con) + "\n" + cut(con, 280) + "\n\n"
                "⚪ <b>Neutral</b> " + exec_icon(neu) + "\n" + cut(neu, 280))

        stat_conf = result.get("stat_confidence")
        stat_expl = result.get("stat_explanation", "")
        stat_line = ""
        if stat_conf is not None:
            icon = "🟢" if stat_conf >= 75 else "🟡" if stat_conf >= 68 else "🔴"
            first_line = stat_expl.split("\n")[0] if stat_expl else ""
            stat_line = ("\n\n📊 <b>STATISTICAL OVERLAY</b>\n"
                         + icon + " Confidence điều chỉnh: <b>" + str(stat_conf) + "%</b>\n"
                         + "<i>" + first_line + "</i>")

        msg4 = (sig_icon + " <b>STAGE 5 — CIO FINAL VERDICT</b> | " + symbol + "\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n\n"
                + cut(fin, 900)
                + stat_line + "\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━•\n"
                "🤖 <i>12 AI Agents · 4 Stages · SignalBot v6.1</i>")

        return [msg1, msg2, msg3, msg4]
`,
  "telegram_bot.py": `# ═══════════════════════════════════════════════════════════
# 2. TELEGRAM BOT — v6.1 (Channel + MiniApp integration)
# ═══════════════════════════════════════════════════════════
import re
import json
import logging
import requests
from analyzer.config import Config

cfg = Config()
log = logging.getLogger("TelegramBot")


class TelegramBot:
    BASE = "https://api.telegram.org"

    def __init__(self):
        self.token   = cfg.TELEGRAM_TOKEN
        self.chat_id = cfg.TELEGRAM_CHAT_ID

    @staticmethod
    def _to_plain(text: str) -> str:
        t = str(text)
        t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t)
        t = re.sub(r"<[^>]*>", "", t)
        for esc, rep in [("&amp;","&"),("&lt;","<"),("&gt;",">"),("&quot;",'"'),("&#39;","'")]:
            t = t.replace(esc, rep)
        return t.strip()

    @staticmethod
    def _to_safe_html(text: str) -> str:
        ALLOWED_OPEN  = {"b", "i", "code", "pre", "u", "s"}
        ALLOWED_CLOSE = {"/" + t for t in ALLOWED_OPEN}
        t      = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text))
        parts  = re.split(r"(<[^>]{0,100}>)", t)
        result = []
        opened = []

        for part in parts:
            if re.match(r"^<[^>]{0,100}>\$", part):
                inner = part[1:-1].strip().lower()
                if inner in ALLOWED_OPEN:
                    result.append("<" + inner + ">")
                    opened.append(inner)
                elif inner in ALLOWED_CLOSE:
                    tag = inner[1:]
                    if tag in opened:
                        while opened and opened[-1] != tag:
                            result.append("</" + opened.pop() + ">")
                        if opened:
                            opened.pop()
                        result.append("</" + tag + ">")
                else:
                    safe = part.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                    result.append(safe)
            else:
                p = re.sub(r"&(?!(amp|lt|gt|quot|#[0-9]+);)", "&amp;", part)
                p = p.replace("<", "&lt;").replace(">", "&gt;")
                result.append(p)

        for tag in reversed(opened):
            result.append("</" + tag + ">")
        return "".join(result)

    def send_connect_button(self, target_chat_id):
        token = cfg.TELEGRAM_REGISTER_TOKEN
        if not token:
            log.error("Thiếu TELEGRAM_REGISTER_TOKEN")
            return None
        url_app = (cfg.RENDER_EXTERNAL_URL or "https://auto-trade-v6.onrender.com") + "/miniapp/connect"
        payload = {
            "chat_id":      target_chat_id,
            "parse_mode":   "HTML",
            "text":         (
                "👋 <b>Chào mừng đến với SignalBot v6.1!</b>\n\n"
                "🤖 Copy Trade tự động trên BingX Futures\n"
                "🔒 API Key được mã hóa AES-256\n"
                "📊 Hệ thống phân tích 12 AI Agents\n\n"
                "<b>Bấm nút bên dưới để kết nối:</b>"
            ),
            "reply_markup": json.dumps({"inline_keyboard": [[{
                "text":    "🔗 Kết Nối BingX API",
                "web_app": {"url": url_app},
            }]]})
        }
        try:
            r = requests.post(f"{self.BASE}/bot{token}/sendMessage", json=payload, timeout=10)
            if r.ok:
                log.info("✅ Sent connect button to %s", target_chat_id)
                return r.json()
            log.error("Connect button error: %s", r.text[:200])
        except Exception as e:
            log.error("send_connect_button: %s", e)
        return None

    def send_user_dashboard_button(self, target_chat_id):
        token = cfg.TELEGRAM_REGISTER_TOKEN
        if not token:
            return None
        url_app = (cfg.RENDER_EXTERNAL_URL or "https://auto-trade-v6.onrender.com") + "/my-dashboard"
        payload = {
            "chat_id":      target_chat_id,
            "text":         "📊 Xem số dư và vị thế đang mở của bạn:",
            "reply_markup": json.dumps({"inline_keyboard": [[{
                "text":    "📊 Mở Dashboard Của Tôi",
                "web_app": {"url": url_app},
            }]]})
        }
        try:
            requests.post(f"{self.BASE}/bot{token}/sendMessage", json=payload, timeout=5)
        except Exception as e:
            log.error("send_user_dashboard_button: %s", e)

    def send(self, text: str):
        cid = str(self.chat_id).strip()
        if not cid or not self.token:
            log.error("TELEGRAM_TOKEN hoặc CHAT_ID trống")
            return None
        try:
            chat_id = int(cid)
        except ValueError:
            log.error("CHAT_ID sai định dạng: %s", cid)
            return None

        html = self._to_safe_html(text)
        if len(html) > 4096:
            html = html[:4000] + "\n<i>[...]</i>"
        try:
            r = requests.post(
                f"{self.BASE}/bot{self.token}/sendMessage",
                json={"chat_id": chat_id, "text": html,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=15)
            if r.ok:
                return r.json()
            resp = r.json()
            new_cid = resp.get("parameters", {}).get("migrate_to_chat_id")
            if new_cid:
                log.warning("Group migrate! Cập nhật TELEGRAM_CHAT_ID: %d", new_cid)
                self.chat_id = str(new_cid)
                r2 = requests.post(
                    f"{self.BASE}/bot{self.token}/sendMessage",
                    json={"chat_id": new_cid, "text": html,
                          "parse_mode": "HTML", "disable_web_page_preview": True},
                    timeout=15)
                if r2.ok:
                    return r2.json()
            log.warning("Telegram HTML %d: %s", r.status_code, resp.get("description","")[:80])
        except Exception as e:
            log.warning("Telegram HTML error: %s", e)

        plain = self._to_plain(text)
        if len(plain) > 4096:
            plain = plain[:4000] + "\n[...]"
        try:
            r = requests.post(
                f"{self.BASE}/bot{self.token}/sendMessage",
                json={"chat_id": chat_id, "text": plain, "disable_web_page_preview": True},
                timeout=15)
            if r.ok:
                return r.json()
        except Exception as e:
            log.error("Telegram plain error: %s", e)
        return None

    def format_signal(self, data: dict, llm_text: str, llm_name: str) -> str:
        sig   = data["final"]
        plan  = data["plan"]
        fibo  = data.get("fibo", {})
        wy    = data.get("wyckoff", {})
        cvd   = data.get("cvd", {})
        bo    = data.get("breakout", {})
        wh    = data.get("whale", {})
        vol   = data.get("volume_1h", {})
        lvls  = fibo.get("levels", {})
        conf  = data["confidence"]
        atype = data.get("asset_type", "CRYPTO")
        
        sweep = data.get("liquidity_sweep", {})
        ob    = data.get("orderbook", {})

        stat_conf = data.get("stat_confidence")
        stat_note = ""
        if stat_conf is not None and abs(stat_conf - conf) >= 2:
            delta    = round(stat_conf - conf, 1)
            sign     = "+" if delta > 0 else ""
            icon     = "🟢" if stat_conf >= 75 else "🟡" if stat_conf >= 68 else "🔴"
            stat_note = (f"\n  └ {icon} AI Stat: <b>{stat_conf:.1f}%</b> "
                         f"({sign}{delta}% vs raw)")

        candle     = data.get("candle", {})
        ms_4h      = data.get("market_structure_4h", {})
        bull_pats  = candle.get("bull_patterns", [])
        bear_pats  = candle.get("bear_patterns", [])
        candle_bias = candle.get("bias", "NEUTRAL")
        ms_struct   = ms_4h.get("structure", "SIDEWAYS")

        CANDLE_IC = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}
        MS_IC     = {"UPTREND": "🔼", "DOWNTREND": "🔽",
                     "SIDEWAYS": "↔️", "EXPANDING": "↕️", "CONTRACTING": "⏸"}

        candle_line = ""
        if bull_pats or bear_pats:
            pat_text = ", ".join((bull_pats + bear_pats)[:4])
            candle_line = (f"\n  ├ Nến 1H+4H : {CANDLE_IC.get(candle_bias,'⚪')} {candle_bias}"
                           f" | {pat_text}")

        HEADER = {"LONG": "🟢 🚀 <b>TÍN HIỆU MUA</b>",
                  "SHORT": "🔴 📉 <b>TÍN HIỆU BÁN</b>",
                  "WAIT": "🟡 ⏳ <b>ĐANG CHỜ ĐỢI</b>"}
        AICON  = {"CRYPTO": "🪙", "STOCK": "📈", "GOLD": "🥇"}
        ANAME  = {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "BNBUSDT": "BNB Chain",
                  "HYPEUSDT": "HyperLiquid", "TSLA": "Tesla", "NVDA": "NVIDIA",
                  "SPY": "S&P 500 ETF", "QQQ": "Nasdaq 100 ETF", "NCCOGOLD2USD-USDT": "Vàng NCCOGOLD2USD-USDT"}
        WY_IC  = {"ACCUMULATION": "🔵", "MARKUP": "🟢", "RE-ACCUMULATION": "🟩",
                  "DISTRIBUTION": "🔴", "MARKDOWN": "⛔", "TRANSITION": "⚪"}
        BI_IC  = {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}
        TF_IC  = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪"}
        CVD_IC = {"BULLISH": "🟢", "BEARISH": "🔴",
                  "BULLISH_DIV": "💚", "BEARISH_DIV": "❤️", "NEUTRAL": "⚪"}
        PRESS_IC = {"STRONG_BUY": "🟢🟢", "BUY": "🟢",
                    "STRONG_SELL": "🔴🔴", "SELL": "🔴", "NEUTRAL": "⚪"}

        conf_bar  = "█" * int(conf / 10) + "░" * (10 - int(conf / 10))
        tf_line   = "  ".join(
            TF_IC.get(r.get("direction","WAIT"),"⚪") + tf.upper()
            for tf, r in data.get("timeframes", {}).items())
        wy_ph     = wy.get("phase", "?")
        wy_bi     = wy.get("bias", "?")
        cvd_tr    = cvd.get("trend", "?")
        cvd_div   = " ⚠️ DIV!" if cvd.get("divergence") else ""
        golden    = "✅ Golden Zone 0.382-0.618" if fibo.get("in_golden") else "⬜ Ngoài Golden Zone"
        fa        = "⬆️" if fibo.get("trend") == "UPTREND" else "⬇️"

        ev_lines  = ("\n" + "\n".join("   ⚡ " + e for e in wy.get("events", []))
                     if wy.get("events") else "")
        bo_line   = ("\n🚨 <b>BREAKOUT</b>: " + str(bo.get("desc",""))
                     if bo.get("type","NONE") != "NONE" else "")
        wh_line   = ("\n" + str(wh.get("desc","")) if wh.get("detected") else "")
        mkt_line  = ("\n🏛️ " + str(data.get("mkt_note","")) if data.get("mkt_note") else "")
        
        sweep_line = ("\n🔥 <b>BẪY THANH KHOẢN (" + str(sweep.get("type", "")) + ")</b>: Quét râu tại <code>\$" + 
                      str(round(sweep.get("price", 0), 2)) + "</code>" if sweep.get("detected") else "")

        if atype == "CRYPTO":
            oi_sec = (f"  ├ OI Delta : {data.get('oi_delta',0)}% → {data.get('oi_signal','?')}\n"
                      f"  ├ OI Desc  : {data.get('oi_desc','?')}\n"
                      f"  └ Funding  : <code>{data.get('funding',0)}%</code>")
        elif atype == "GOLD":
            oi_sec = "  └ Xem DXY + lãi suất Fed khi giao dịch vàng"
        else:
            oi_sec = "  └ Xem earnings + macro Mỹ khi giao dịch cổ phiếu"

        sym_display = AICON.get(atype,"📊") + " " + ANAME.get(data["symbol"], data["symbol"])
        llm_clean   = self._to_safe_html(llm_text or "")
        
        ob_section = []
        if ob.get("detected"):
            obi = ob.get("imbalance", 0)
            obi_icon = "🟢" if obi > 0 else ("🔴" if obi < 0 else "⚪")
            ob_section = [
                "",
                "🧱 <b>SỔ LỆNH L2 (ORDER BOOK)</b>",
                f"  ├ Tường Bán (Cản) : <code>\${ob.get('resist_wall')}</code>",
                f"  ├ Tường Mua (Đỡ)  : <code>\${ob.get('support_wall')}</code>",
                f"  └ Mất cân bằng    : {obi_icon} <b>{obi}</b>"
            ]

        lines = [
            HEADER.get(sig, sig) + " — " + sym_display,
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💰 Giá       : <code>\${round(data['price'],2)}</code>",
            f"🎯 Độ tin cậy: <b>{conf}%</b> <code>[{conf_bar}]</code>" + stat_note,
            "📊 Khung giờ : " + tf_line,
            bo_line + wh_line + mkt_line + sweep_line,
            "",
            "⚡ <b>SMART SIGNALS</b>",
            "  ├ CVD      : " + CVD_IC.get(cvd_tr,"⚪") + " " + cvd_tr + cvd_div,
            "  ├ Vol      : " + str(vol.get("vol_trend","?")) + " x" + str(vol.get("vol_ratio",1)),
            "  ├ OBV      : " + str(vol.get("obv_trend","?")),
            "  ├ Pressure : " + PRESS_IC.get(vol.get("pressure","NEUTRAL"),"⚪") + " " +
                               str(vol.get("pressure","?")) + " (" + str(vol.get("buy_pct",50)) + "% buy)",
            "  ├ VWAP     : <code>\$" + str(vol.get("vwap",0)) + "</code> (" + str(vol.get("vwap_signal","?")) + ")",
            "  ├ POC      : <code>\$" + str(vol.get("poc",0)) + "</code>",
            candle_line,
            "  ├ MS 4H    : " + MS_IC.get(ms_struct,"↔️") + " " + ms_struct +
                               (" ⚡BOS" if ms_4h.get("bos") else ""),
            oi_sec,
            "",
            "📐 <b>FIBONACCI (1H)</b>",
            "  ├ Xu hướng : " + fa + " " + str(fibo.get("trend","?")),
            "  ├ Vùng giá : <code>" + str(fibo.get("zone","?")) + "</code>",
            "  ├ " + golden,
            "  ├ 0.382 ▶ <code>\$" + str(lvls.get("0.382","?")) + "</code>",
            "  ├ 0.618 ▶ <code>\$" + str(lvls.get("0.618","?")) + "</code>",
            "  └ Ext 1.618: <code>\$" + str(lvls.get("1.618","?")) + "</code>",
            "",
            "📊 <b>WYCKOFF (4H)</b>",
            "  ├ Pha    : " + WY_IC.get(wy_ph,"⚪") + " <b>" + wy_ph + "</b>",
            "  ├ Hướng  : " + BI_IC.get(wy_bi,"➡️") + " " + wy_bi,
            "  ├ Volume : " + str(wy.get("vol_trend","?")) + " | Pos: " + str(wy.get("pos_range","?")) + "%",
            "  └ " + str(wy.get("action","?")),
            ev_lines,
        ]
        
        lines.extend(ob_section)
        

        bayes_ev = data.get("bayes_ev")
        if bayes_ev:
            lines.extend([
                "",
                "🎲 <b>XÁC SUẤT (BAYES) & EV</b>",
                "  ├ P(Win)    : <b>" + str(bayes_ev.get("p_win")) + "%</b>",
                "  ├ Likelihood: " + str(bayes_ev.get("likelihood")),
                "  └ Kỳ vọng EV: <b>" + str(bayes_ev.get("ev_ratio")) + " R</b>" + (" ✅" if bayes_ev.get("ev_ratio", 0) > 0.15 else " ⚠️"),
            ])
        lines.extend([
            "",

            "📌 <b>KẾ HOẠCH LỆNH</b>",
            "  ├ Vào lệnh  : <code>\$" + str(plan["entry"]) + "</code>",
            "  ├ 🛑 Cắt lỗ: <code>\$" + str(plan["sl"]) + "</code> (-" + str(data.get("sl_pct","1.5")) + "%)",
            "  ├ 🎯 TP1    : <code>\$" + str(plan["tp1"]) + "</code>",
            "  ├ 🏆 TP2    : <code>\$" + str(plan["tp2"]) + "</code>",
            "  └ ⚖️ R:R     : 1:" + str(data.get("rr_ratio","2.0")),
            "",
            "🤖 <b>AI</b> <i>(" + llm_name + ")</i>",
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
            llm_clean,
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
            "",
            "⏰ " + data.get("timestamp","") + "  |  💼 " + str(data.get("sl_pct","2")) + "% rủi ro/lệnh",
        ])
        return "\n".join(lines)
`,
  "main_scanner.py": `# ═══════════════════════════════════════════════════════════
# MAIN SCANNER — SignalBot v6.1 (Analyzer Core)
# ═══════════════════════════════════════════════════════════
import os, time, json, logging, schedule, gc, requests, threading
from datetime import datetime as dt_module
import redis

from analyzer.engine import SignalEngine
from analyzer.llm_agents import LLMChain, MultiAgentPipeline
from analyzer.telegram_bot import TelegramBot
from analyzer.config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
cfg = Config()


def get_memory_for_ai(symbol: str) -> str:
    try:
        from core_api.main import SessionLocal, TradeJournal
        db = SessionLocal()
        lessons = db.query(TradeJournal).filter(
            TradeJournal.symbol == symbol,
            TradeJournal.pnl_pct < 0
        ).order_by(TradeJournal.timestamp.desc()).limit(3).all()
        db.close()
        if not lessons:
            return "Chưa có cảnh báo nào từ dữ liệu quá khứ."
        mem = "⚠️ CẢNH BÁO TỪ QUÁ KHỨ (CÁC LỆNH BỊ CẮT LỖ GẦN NHẤT):\n"
        for l in lessons:
            mem += f"- Lần trước đánh {l.direction}: {l.lesson}\n"
        return mem
    except Exception as e:
        logging.getLogger("SignalBot").warning(f"Không thể lấy trí nhớ AI: {e}")
        return ""


class SignalBot:
    CRYPTO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "HYPEUSDT"]
    STOCK_SYMBOLS  = ["TSLA", "NVDA", "SPY", "QQQ", "NCCOGOLD2USD-USDT"]

    _PIPELINE_SEMAPHORE = threading.Semaphore(4)  # Tăng lên 4 luồng song song

    def __init__(self):
        self.log = logging.getLogger("SignalBot")
        self.dt  = dt_module

        self.engine       = SignalEngine()
        self.llm          = LLMChain()
        self.tg           = TelegramBot()
        self.pipeline     = MultiAgentPipeline(self.llm)
        self.last_signals = {}
        self._scan_count  = 0
        self._closed_notified = set()

        self._pushed_signals: dict[str, float] = {}
        self._push_cooldown = 90

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        try:
            self.redis_client = redis.from_url(redis_url, socket_connect_timeout=5)
            self.redis_client.ping()
            self.log.info("🟢 Redis Queue kết nối OK")
        except Exception as e:
            self.log.error("❌ Lỗi kết nối Redis: %s", e)
            self.redis_client = None

    def _cleanup(self):
        keys = self.CRYPTO_SYMBOLS + self.STOCK_SYMBOLS
        self.last_signals    = {k: v for k, v in self.last_signals.items() if k in keys}
        self._pushed_signals = {k: v for k, v in self._pushed_signals.items() if k in keys}
        gc.collect()
        self.log.info("🧹 RAM đã dọn dẹp")

    def should_send(self, symbol, new):
        sweep = new.get("liquidity_sweep", {})
        if sweep.get("detected"):
            return True, f"🔥 LIQUIDITY SWEEP ({sweep.get('type')})"
        prev = self.last_signals.get(symbol)
        if not prev or not prev.get("final"):
            return True, "lần đầu"
        if prev["final"] != new["final"]:
            return True, "signal đổi " + prev["final"] + "→" + new["final"]
        if prev.get("wyckoff", {}).get("phase") != new.get("wyckoff", {}).get("phase"):
            return True, "Wyckoff đổi pha"
        bo = new.get("breakout", {})
        if bo.get("type", "NONE") != "NONE" and bo.get("strength", 0) >= 60:
            return True, "Breakout " + bo["type"]
        if new.get("whale", {}).get("detected"):
            return True, "Whale detected"
        if new["confidence"] >= 80 and new["final"] != "WAIT":
            return True, "confidence cao"
        return False, "không đổi"

    def push_to_queue(self, symbol, data):
        if not self.redis_client:
            self.log.warning("⚠️ Không có Redis. Không thể đẩy lệnh.")
            return

        now = time.time()
        last_push = self._pushed_signals.get(symbol, 0)
        if now - last_push < self._push_cooldown:
            remaining = int(self._push_cooldown - (now - last_push))
            self.log.info("  ⏭️ Bỏ qua push %s — cooldown còn %ds", symbol, remaining)
            return

        payload = {
            "signal_id": f"sig_{int(now)}_{symbol}",
            "symbol":    symbol,
            "asset_type": data.get("asset_type", "CRYPTO"),
            "final":     data["final"],
            "confidence": data["confidence"],
            "plan":      data["plan"],
            "timestamp": data.get("timestamp", now),
        }
        try:
            self.redis_client.lpush("TRADE_SIGNALS", json.dumps(payload))
            self.redis_client.ltrim("TRADE_SIGNALS", 0, 99)
            self._pushed_signals[symbol] = now
            self.log.info(f"📤 Đã đẩy lệnh {data['final']} {symbol} vào Hàng đợi.")
        except Exception as e:
            self.log.error(f"❌ Lỗi đẩy Redis: {e}")

    def _run_pipeline_sync(self, sym, data):
        final_sig = data.get("final", "WAIT")
        conf = data.get("confidence", 0)
        ev_ratio = data.get("bayes_ev", {}).get("ev_ratio", 0)
        if final_sig == "WAIT" and conf < 70 and ev_ratio < 0.3:
            return
        with self._PIPELINE_SEMAPHORE:
            try:
                result = self.pipeline.run(data)
                msgs   = MultiAgentPipeline.format_telegram(result, sym)
                for i, msg in enumerate(msgs):
                    self.tg.send(msg)
                    if i < len(msgs) - 1:
                        time.sleep(1)
                self.log.info("🤖 Pipeline %s hoàn thành (%d msgs)", sym, len(msgs))
            except Exception as e:
                self.log.error("❌ Pipeline %s: %s", sym, e)

    def _run_pipeline(self, sym, data):
        t = threading.Thread(
            target=self._run_pipeline_sync,
            args=(sym, data),
            name=f"pipeline-{sym}",
            daemon=True,
        )
        t.start()
        self.log.info("  🔀 Pipeline %s → background thread [%s]", sym, t.name)

    def _scan(self, symbols, label):
        self.log.info("─── %s ───", label)
        for sym in symbols:
            try:
                tradeable, note = self.engine.is_tradeable(sym)

                if tradeable and sym in self._closed_notified:
                    self._closed_notified.discard(sym)
                    self.log.info("  🔔 %s: Thị trường mở lại", sym)

                if not tradeable:
                    self.log.info("  ⏸️  %s: %s", sym, note)
                    if sym not in self._closed_notified:
                        self.tg.send("⏸️ <b>" + sym + "</b> — " + note + "\nBot tự phân tích khi mở lại.")
                        self._closed_notified.add(sym)
                    continue

                data = self.engine.full_analysis(sym)
                data["ai_memory"] = get_memory_for_ai(sym)

                final_sig = data.get("final", "WAIT")
                conf = data.get("confidence", 0)
                ev_ratio = data.get("bayes_ev", {}).get("ev_ratio", 0)
                
                # [LỌC SỚM TÀI NGUYÊN] Bỏ qua AI cho lệnh WAIT có toán học quá yếu
                if final_sig == "WAIT" and ev_ratio < 0.2 and conf < 65:
                    self.log.info("  ⏭️ [Early Filter] Bỏ qua AI cho %s (EV: %.2f, Conf: %.1f%%) để tiết kiệm server", sym, ev_ratio, conf)
                    time.sleep(1.0)
                    continue

                llm_text, llm = self.llm.analyze(data)
                v1h = data.get("volume_1h", {})

                self.log.info("  %s: %s %.1f%% | CVD:%s | Vol:%s(%.1fx) | Press:%s | LLM:%s",
                              sym, data["final"], data["confidence"],
                              data.get("cvd", {}).get("trend", "?"),
                              v1h.get("vol_trend", "?"), v1h.get("vol_ratio", 1),
                              v1h.get("pressure", "?"), llm)

                should, reason = self.should_send(sym, data)
                if should:
                    msg = self.tg.format_signal(data, llm_text, llm)
                    self.tg.send(msg)
                    self.last_signals[sym] = data
                    self.log.info("  📱 Đã gửi [%s]: %s", reason, sym)

                    if data["final"] != "WAIT":
                        self.push_to_queue(sym, data)

                    self._run_pipeline(sym, data)
                else:
                    self.log.info("  ⏭️  Bỏ qua [%s]: %s %s %.1f%%",
                                  reason, sym, data["final"], data["confidence"])

            except Exception as e:
                self.log.error("  ❌ Lỗi xử lý %s: %s", sym, e)

            time.sleep(1.5)

    def run_crypto(self):
        self._scan_count += 1
        self.log.info("═" * 45)
        self.log.info("🔄 Scan #%d — %s", self._scan_count, self.dt.now().strftime("%H:%M:%S"))
        try:
            self._scan(self.CRYPTO_SYMBOLS, "CRYPTO BTC·ETH·BNB")
        except Exception as e:
            self.log.error("❌ run_crypto: %s", e)
        if self._scan_count % 8 == 0:
            self._cleanup()

    def run_stocks(self):
        try:
            self.log.info("═" * 45)
            self.log.info("📈 Stock+Gold — %s", self.dt.now().strftime("%H:%M:%S"))
            self._scan(self.STOCK_SYMBOLS, "STOCK+GOLD")
        except Exception as e:
            self.log.error("❌ run_stocks: %s", e)

    def _hourly_report(self):
        try:
            if not self.last_signals:
                return
            now = self.dt.now().strftime("%d/%m/%Y %H:%M")
            SIG = {"LONG": "🚀", "SHORT": "📉", "WAIT": "⏳"}
            WY  = {"ACCUMULATION": "🔵", "MARKUP": "🟢", "RE-ACCUMULATION": "🟩",
                   "DISTRIBUTION": "🔴", "MARKDOWN": "⛔", "TRANSITION": "⚪"}
            rows = ["📋 <b>BÁO CÁO ĐỊNH KỲ</b>", "🕐 " + now, "━━━━━━━━━━━━━━━━━━━━━━━━━", "🪙 <b>CRYPTO</b>"]
            for sym in self.CRYPTO_SYMBOLS:
                d = self.last_signals.get(sym)
                if not d or not d.get("final"): continue
                wy  = d.get("wyckoff", {}).get("phase", "?")
                bar = "█" * int(d["confidence"] / 10) + "░" * (10 - int(d["confidence"] / 10))
                rows.append("  " + SIG.get(d["final"], "❓") + " <b>" + sym[:3] + "</b> <code>\$" +
                             str(round(d["price"], 2)) + "</code> <b>" + d["final"] + "</b> " +
                             str(d["confidence"]) + "% [" + bar + "] " + WY.get(wy, "⚪") + wy)
            rows.append("\n📈 <b>CỔ PHIẾU MỸ</b>")
            for sym in ["TSLA", "NVDA", "SPY", "QQQ", "NCCOGOLD2USD-USDT"]:
                d = self.last_signals.get(sym)
                if not d or not d.get("final"): continue
                wy = d.get("wyckoff", {}).get("phase", "?")
                rows.append("  " + SIG.get(d["final"], "❓") + " <b>" + sym + "</b> <code>\$" +
                             str(round(d["price"], 2)) + "</code> <b>" + d["final"] + "</b> " +
                             str(d["confidence"]) + "% " + WY.get(wy, "⚪") + wy)
            rows += ["\n━━━━━━━━━━━━━━━━━━━━━━━━━", "✅ SignalBot v6.1 Core đang chạy bình thường"]
            self.tg.send("\n".join(rows))
            self.log.info("📋 Báo cáo định kỳ đã gửi")
        except Exception as e:
            self.log.error("❌ Hourly report: %s", e)

    def _self_ping(self):
        try:
            url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
            if not url: return
            for attempt in range(3):
                try:
                    r = requests.get(url, timeout=10)
                    self.log.info("🏓 Self-ping OK (%d)", r.status_code)
                    return
                except Exception:
                    if attempt < 2: time.sleep(3)
        except Exception as e:
            self.log.error("❌ Ping: %s", e)

    def _build_schedule(self):
        schedule.clear()
        schedule.every(15).minutes.do(self.run_crypto)
        schedule.every(30).minutes.do(self.run_stocks)
        schedule.every().hour.at(":30").do(self._hourly_report)
        schedule.every(4).minutes.do(self._self_ping)

    def start(self):
        self.log.info("━" * 45)
        self.log.info("🚀 SignalBot v6.1 (Analyzer Core) khởi động!")
        self.log.info("━" * 45)

        self._self_ping()
        self.run_crypto()
        self.run_stocks()

        self._build_schedule()
        self.log.info("📅 Schedule: Crypto/15p · Stock+Gold/30p · Ping/4p · Report/giờ")

        consecutive_errors = 0
        while True:
            try:
                schedule.run_pending()
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                self.log.error("❌ Schedule #%d: %s", consecutive_errors, e)
                if consecutive_errors >= 10:
                    self.log.warning("⚠️  Rebuild schedule...")
                    try:
                        self._build_schedule()
                        consecutive_errors = 0
                    except Exception as e2:
                        self.log.error("❌ Rebuild: %s", e2)

            time.sleep(10)
`,
  "main.py": `""" API"""
import os
import sys
import json
import asyncio
import threading
import time
import logging
import gc
import schedule
import requests as _req
import redis
import redis.asyncio as aioredis
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core_api.models import SessionLocal, User, TradeJournal
from core_api.security import encrypt_api_secret, decrypt_api_secret
from analyzer.main_scanner import SignalBot
from worker.bingx_trader import BingXExchange

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("MainAPI")

app = FastAPI(title="SignalBot v6.1")

REGISTER_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
except Exception:
    redis_client = None

BOT_GLOBAL_AUTO = False
BOT_KILL_SWITCH = False
_POS_LOCK = threading.Lock()
LIVE_POSITIONS = {}
LAST_SIGNALS = []
_LAST_REVERSAL_EVAL = {}

# ══════════════════════════════════════════════════════════════════
# TIER CONFIG
# ══════════════════════════════════════════════════════════════════
TIER_CONFIG = {
    "TIER1": {
        "label": "Ca Con", "min_capital": 0, "max_capital": 500,
        "min_confidence": 68.0, "max_risk_pct": 2.0,
        "max_positions": 2, "leverage": 5, "target_monthly": "5-8%",
    },
    "TIER2": {
        "label": "Tieu Chuan", "min_capital": 500, "max_capital": 2000,
        "min_confidence": 75.0, "max_risk_pct": 1.5,
        "max_positions": 3, "leverage": 5, "target_monthly": "4-6%",
    },
    "TIER3": {
        "label": "Ca Map", "min_capital": 2000, "max_capital": float("inf"),
        "min_confidence": 80.0, "max_risk_pct": 1.0,
        "max_positions": 5, "leverage": 3, "target_monthly": "3-5%",
    },
}
MIN_CAPITAL_TO_TRADE = 20.0

def get_tier(capital: float) -> Optional[str]:
    if capital < MIN_CAPITAL_TO_TRADE:
        return None
    for tier, cfg in TIER_CONFIG.items():
        if cfg["min_capital"] <= capital < cfg["max_capital"]:
            return tier
    return "TIER3"

def apply_tier(user: User, tier: str):
    if tier not in TIER_CONFIG:
        return
    cfg = TIER_CONFIG[tier]
    user.tier           = tier
    user.min_confidence = cfg["min_confidence"]
    user.max_risk_pct   = cfg["max_risk_pct"]
    user.max_positions  = cfg["max_positions"]
    user.leverage       = cfg["leverage"]

def get_bx(user: User) -> BingXExchange:
    secret = decrypt_api_secret(user.api_secret_encrypted) if user.api_secret_encrypted else ""
    return BingXExchange(user.api_key, secret)

def _update_user_balance_and_tier(user: User, balance: float, db: Session):
    try:
        user.capital = balance
        tier = get_tier(balance)
        if tier and user.tier != tier:
            apply_tier(user, tier)
        db.commit()
    except Exception as e:
        log.warning("Update balance error: %s", e)

def _tg_send(token: str, chat_id: str, text: str):
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        _req.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        log.warning("_tg_send error: %s", e)

def _save_journal(uid: str, sym: str, direction: str, pnl_pct: float, qty: float):
    try:
        db = SessionLocal()
        user = db.query(User).filter(User.telegram_id == uid).first()
        j = TradeJournal(
            symbol=sym,
            user_id=uid,
            tier=user.tier if user else "TIER1",
            direction=direction,
            outcome="WIN" if pnl_pct > 0 else "LOSS",
            pnl_pct=pnl_pct,
            pnl_usd=0.0,
            lesson="Early Exit"
        )
        db.add(j)
        db.commit()
        db.close()
    except Exception as e:
        pass

def run_signal_bot():
    try:
        bot = SignalBot()
        bot.start()
    except Exception as e:
        log.error("run_signal_bot: %s", e)

def run_trade_worker():
    pass

def _tp1_monitor():
    global LIVE_POSITIONS
    while True:
        try:
            with _POS_LOCK:
                positions = LIVE_POSITIONS.copy()
            
            db = SessionLocal()
            for user_id, user_positions in positions.items():
                if not user_positions: continue
                user = db.query(User).filter_by(telegram_id=user_id).first()
                if not user or not user.api_key: continue
                
                bx = get_bx(user)
                
                for p in user_positions:
                    sym = p.get("symbol")
                    direction = p.get("direction")
                    cur_price = p.get("current_price", 0)
                    tp1 = p.get("tp1", 0)
                    tp2 = p.get("tp2", 0)
                    entry = p.get("entry", 0)
                    qty = p.get("qty", 0)
                    
                    if not sym or not cur_price or not tp1 or qty <= 0:
                        continue
                        
                    # Tránh trigger nhiều lần
                    redis_key = f"TP1_HIT:{user_id}:{sym}:{direction}"
                    if redis_client and redis_client.exists(redis_key):
                        continue
                        
                    is_hit = False
                    if direction == "LONG" and cur_price >= tp1 and tp1 > entry:
                        is_hit = True
                    elif direction == "SHORT" and cur_price <= tp1 and tp1 < entry:
                        is_hit = True
                        
                    if is_hit:
                        res = bx.handle_tp1_hit(sym, direction, qty, entry, tp2)
                        if res.get("ok"):
                            if redis_client:
                                redis_client.setex(redis_key, 86400, "1")  # Lưu 1 ngày
                            _tg_send(
                                REGISTER_TOKEN, user_id,
                                f"🎯 <b>TP1 HIT: {sym}</b>\n"
                                f"📈 Đã chốt 50% vị thế tại \${tp1:.4f}\n"
                                f"🛡️ Đã dời SL về Entry (\${entry:.4f}) để bảo toàn vốn."
                            )
            db.close()
        except Exception as e:
            log.error("_tp1_monitor error: %s", e)
        time.sleep(15)

def _schedule_weekly_report():
    pass

def _register_telegram_webhook():
    try:
        url = f"https://api.telegram.org/bot{REGISTER_TOKEN}/setWebhook"
        webhook_url = f"{os.getenv('RENDER_URL', 'https://auto-trade-v6.onrender.com')}/telegram/webhook"
        r = _req.get(url, params={"url": webhook_url}, timeout=10)
        if r.status_code == 200:
            log.info("✅ Auto register Telegram Webhook success: %s", webhook_url)
    except Exception as e:
        log.warning("⚠️ Error registering Telegram Webhook: %s", e)

def evaluate_reversal_for_position(user: User, pos: dict, current_price: float, db: Session):
    sym = pos["symbol"]
    direction = pos["direction"]
    qty = float(pos.get("qty", 0))
    entry = float(pos.get("entry", 0))
    
    now = time.time()
    
    try:
        cached = _LAST_REVERSAL_EVAL.get(sym)
        if cached and isinstance(cached, dict) and now - cached.get("time", 0) < 180:
            new_direction = cached.get("direction", "WAIT")
            conf = cached.get("conf", 0)
            analysis = cached.get("analysis", {})
        else:
            from analyzer.engine import SignalEngine
            engine = SignalEngine()
            analysis = engine.full_analysis(sym)
            new_direction = analysis.get("final", "WAIT")
            conf = analysis.get("confidence", 0)
            
            _LAST_REVERSAL_EVAL[sym] = {
                "time": now,
                "direction": new_direction,
                "conf": conf,
                "analysis": analysis
            }
            
        is_reversal = (direction == "LONG" and new_direction == "SHORT") or (direction == "SHORT" and new_direction == "LONG")
        is_weak_trend = (new_direction == "WAIT" and conf < 40)
        
        should_close_early = False
        should_reverse = False
        
        if is_reversal and conf >= 70:
            should_close_early = True
            should_reverse = True
        elif is_reversal and conf >= 40:
            should_close_early = True
        elif is_weak_trend:
            should_close_early = True
            
        if should_close_early:
            bx = get_bx(user)
            in_profit = (direction == "LONG" and current_price > entry) or (direction == "SHORT" and current_price < entry)
            pnl_pct = ((current_price - entry) / entry * 100 if direction == "LONG" else (entry - current_price) / entry * 100)
            
            action_type = "CHỐT LỜI SỚM" if in_profit else "CẮT LỖ SỚM"
            emoji = "💰" if in_profit else "⚠️"
            reason = "đảo chiều mạnh" if should_reverse else ("đảo chiều yếu" if is_reversal else "xu hướng suy yếu (WAIT)")
            
            log.info("🚨 Early Exit detected for %s %s: %s (Reason: %s)", user.telegram_id, sym, action_type, reason)
            
            res = bx.close_position(sym, qty, direction)
            if res.get("ok"):
                if redis_client:
                    try:
                        redis_client.setex(f"REVERSAL_CLOSED:{user.telegram_id}:{sym}:{direction}", 120, "1")
                    except Exception:
                        pass
                
                bx.cancel_all_orders(sym)
                
                _tg_send(
                    REGISTER_TOKEN, user.telegram_id,
                    f"{emoji} <b>{action_type} ({reason.upper()}): {sym}</b>\n\n"
                    f"🔄 Đánh giá lại: Xu hướng chuyển sang <b>{new_direction}</b> (Conf: {conf}%).\n"
                    f"📊 Vị thế cũ: {direction} @ \${entry:.4f}\n"
                    f"📈 Giá hiện tại: \${current_price:.4f} | PnL: {pnl_pct:+.2f}%\n"
                    f"🔒 Đã tự động đóng vị thế và huỷ SL/TP cũ để bảo vệ vốn."
                )
                
                _save_journal(user.telegram_id, sym, direction, pnl_pct, qty)
                time.sleep(1.5)
                
                if should_reverse:
                    new_entry = float(analysis["plan"]["entry"])
                    new_sl    = float(analysis["plan"]["sl"])
                    new_tp1   = float(analysis["plan"]["tp1"])
                    new_tp2   = float(analysis["plan"].get("tp2", 0))
                    if new_tp2 <= 0:
                        if new_direction == "LONG":
                            new_tp2 = round(new_tp1 + abs(new_tp1 - new_entry), 4)
                        else:
                            new_tp2 = round(new_tp1 - abs(new_tp1 - new_entry), 4)
                    
                    sl_pct = abs(new_entry - new_sl) / new_entry
                    if sl_pct >= 0.001:
                        risk_amt = user.capital * (user.max_risk_pct / 100)
                        new_qty = round(risk_amt / (new_entry * sl_pct), 4)
                        if new_qty > 0:
                            bx.set_leverage(sym, leverage=user.leverage)
                            bx.cancel_all_orders(sym)
                            new_order_res = bx.place_order(sym, "BUY" if new_direction == "LONG" else "SELL", new_qty, new_sl, new_tp2)
                            if new_order_res.get("ok"):
                                _tg_send(
                                    REGISTER_TOKEN, user.telegram_id,
                                    f"🚀 <b>VÀO LỆNH THEO XU HƯỚNG MỚI: {sym}</b>\n"
                                    f"📈 {new_direction} | Conf: {conf:.1f}%\n"
                                    f"💰 Qty: {new_qty:.4f} | Lev: {user.leverage}x\n"
                                    f"🛑 SL: <code>\${new_sl:.4f}</code>\n"
                                    f"🎯 TP1: <code>\${new_tp1:.4f}</code> | TP2: <code>\${new_tp2:.4f}</code>"
                                )
    except Exception as e:
        log.warning("Evaluate reversal for %s %s error: %s", user.telegram_id, sym, e)


# ══════════════════════════════════════════════════════════════════
# SYNC POSITIONS & BALANCE
# ══════════════════════════════════════════════════════════════════
def sync_bingx_positions():
    global LIVE_POSITIONS
    _bx_cache: dict = {}
    cleanup_counter = 0

    while True:
        try:
            db           = SessionLocal()
            active_users = db.query(User).filter(User.is_active == True).all()

            current_all: list = []
            current_map: dict = {}

            for user in active_users:
                tid = user.telegram_id
                try:
                    if tid not in _bx_cache:
                        _bx_cache[tid] = get_bx(user)
                    bx = _bx_cache[tid]

                    balance = bx.get_balance()
                    if balance > 0 and abs(balance - (user.capital or 0)) / max(user.capital or 1, 1) > 0.02:
                        _update_user_balance_and_tier(user, balance, db)

                    if user.capital < MIN_CAPITAL_TO_TRADE:
                        continue

                    positions = bx.get_open_positions()
                    triggers  = bx.get_trigger_orders()

                    if not isinstance(positions, list):
                        positions = []
                    if not isinstance(triggers, dict):
                        triggers = {}

                    for p in positions:
                        if not isinstance(p, dict):
                            continue
                        sym  = p.get("symbol", "")
                        if not sym:
                            continue
                        cur  = bx.get_latest_price(sym) or p.get("entry", 0)
                    
                        # Evaluate for reversal / early close / lock profit
                        evaluate_reversal_for_position(user, p, cur, db)
                        trig = triggers.get(sym, {})
                        if not isinstance(trig, dict):
                            trig = {}
                        sl   = trig.get("sl",  p.get("entry", 0) * (0.98 if p.get("direction") == "LONG" else 1.02))
                        tp2  = trig.get("tp2", p.get("entry", 0) * (1.05 if p.get("direction") == "LONG" else 0.95))
                        tp1  = p.get("entry", 0) * (1.025 if p.get("direction", "LONG") == "LONG" else 0.975)
                        pnl  = p.get("pnl", 0)
                        margin = user.capital * (user.max_risk_pct / 100)
                        pct  = round(pnl / margin * 100, 2) if margin > 0 else 0

                        pos_key = f"{tid}_{sym}_{p.get('direction', 'LONG')}"
                        current_map[pos_key] = {
                            "direction": p.get("direction", "LONG"), "pct": pct,
                            "qty": p.get("qty", 0), "user_id": tid,
                            "entry": p.get("entry", 0), "sl": sl, "tp2": tp2,
                        }
                        current_all.append({
                            "user_id": tid, "tier": user.tier, "capital": user.capital,
                            "symbol": sym, "direction": p.get("direction", "LONG"), "entry": p.get("entry", 0),
                            "current_price": cur, "pnl": pnl, "pnl_pct": pct,
                            "qty": p.get("qty", 0), "sl": sl, "tp1": tp1, "tp2": tp2,
                        })
                except Exception as e:
                    log.warning("Sync user %s: %s", tid, e)
                    _bx_cache.pop(tid, None)

            prev_map = getattr(sync_bingx_positions, "_prev", {})
            for k, v in prev_map.items():
                if k not in current_map:
                    parts = k.split("_", 2)
                    if len(parts) == 3:
                        user_id, symbol, direction = parts[0], parts[1], parts[2]
                        pnl_pct = v.get("pct", 0)
                        qty = v.get("qty", 0)
                        entry = v.get("entry", 0)
                        sl = v.get("sl", 0)
                        tp2 = v.get("tp2", 0)

                        _save_journal(user_id, symbol, direction, pnl_pct, qty)

                        # Check if this close was already notified by reversal
                        was_reversal = False
                        if redis_client:
                            try:
                                rev_key = f"REVERSAL_CLOSED:{user_id}:{symbol}:{direction}"
                                if redis_client.get(rev_key):
                                    was_reversal = True
                                    redis_client.delete(rev_key)
                            except Exception:
                                pass

                        if not was_reversal:
                            # Send Telegram notification for Closed Position!
                            # Determine if it hit SL, TP2, or was closed manually
                            outcome_emoji = "🏆" if pnl_pct > 0 else "🛑"
                            outcome_text = "CHỐT LỜI THÀNH CÔNG (TP2)" if pnl_pct > 0 else "DỪNG LỖ (SL)"
                            if abs(pnl_pct) < 0.1:
                                outcome_emoji = "🛡️"
                                outcome_text = "HOÀ VỐN / ĐÓNG THỦ CÔNG"

                            pnl_usd = 0
                            try:
                                user_db = db.query(User).filter(User.telegram_id == user_id).first()
                                if user_db:
                                    pnl_usd = user_db.capital * (user_db.max_risk_pct / 100) * pnl_pct / 100
                            except Exception as e:
                                log.error("Error getting user_db for closing notification: %s", e)

                            _tg_send(
                                REGISTER_TOKEN, user_id,
                                f"{outcome_emoji} <b>VỊ THẾ ĐÃ ĐÓNG: {symbol}</b>\n\n"
                                f"📈 Hướng: <b>{direction}</b>\n"
                                f"💰 Khối lượng: {qty:.4f} {symbol}\n"
                                f"📊 PnL: <b>{pnl_pct:+.2f}% ({'+' if pnl_usd >= 0 else ''}\${pnl_usd:.2f})</b>\n"
                                f"🛑 SL cũ: <code>\${sl:.4f}</code> | 🏆 Target: <code>\${tp2:.4f}</code>\n"
                                f"🎯 Kết quả: <b>{outcome_text}</b>"
                            )

            sync_bingx_positions._prev = current_map

            with _POS_LOCK:
                LIVE_POSITIONS = current_all

            db.close()

        except Exception as e:
            log.error("sync_bingx_positions: %s", e)

        cleanup_counter += 1
        if cleanup_counter >= 2880:
            cleanup_counter = 0
            threading.Thread(target=_cleanup_inactive_users, daemon=True).start()

        time.sleep(30)


def _save_journal(user_id: str, symbol: str, direction: str, pnl_pct: float, qty: float):
    db = SessionLocal()
    try:
            user    = db.query(User).filter(User.telegram_id == user_id).first()
            tier    = user.tier if user else "TIER1"
            capital = user.capital if user else 0
            pnl_usd = capital * (user.max_risk_pct / 100) * pnl_pct / 100 if user else 0

            result = "WIN" if pnl_pct > 0 else "LOSS"
            lesson = (f"Lệnh {direction} {result} {round(abs(pnl_pct),2)}%. "
                      + ("Xu hướng & timing tốt." if result == "WIN"
                         else "Kiểm tra CVD, volume, Wyckoff trước khi vào tiếp."))
            outcome = "TP" if pnl_pct > 0 else "SL"

            db.add(TradeJournal(
                symbol=symbol, user_id=user_id, tier=tier, direction=direction,
                outcome=outcome, pnl_pct=pnl_pct, pnl_usd=pnl_usd,
                context=f"{symbol} {direction} @ {datetime.now().strftime('%d/%m %H:%M')}",
                lesson=lesson))
            if user:
                user.total_pnl = (user.total_pnl or 0) + pnl_usd

            old = (db.query(TradeJournal).filter(TradeJournal.user_id == user_id)
                   .order_by(TradeJournal.timestamp.desc()).all())
            if len(old) > 50:
                for r in old[50:]:
                    db.delete(r)
            db.commit()

            if redis_client:
                try:
                    redis_client.delete(f"TP1_DONE:{user_id}:{symbol}:{direction}")
                except Exception:
                    pass

    except Exception as e:
        db.rollback()
        log.error("_save_journal: %s", e)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════
# TRADE WORKER
# ══════════════════════════════════════════════════════════════════
async def _trade_worker_async():
    log.info("Trade Worker khoi dong...")
    retry = 0
    while True:
        r = None
        try:
            # Dung cac thong so socket_timeout va socket_keepalive de tranh timeout ngat ket noi voi Upstash/Redis
            r = await aioredis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=10,
                socket_timeout=15,
                socket_keepalive=True,
                retry_on_timeout=True
            )
            await r.ping()
            log.info("Worker Redis OK")
            retry = 0
            while True:
                try:
                    # Giam timeout blpop xuong 2 giay de giu cho socket luon hoat dong va tranh socket read timeout (5s)
                    msg = await r.blpop("TRADE_SIGNALS", timeout=2)
                except (redis.exceptions.TimeoutError, asyncio.TimeoutError):
                    # Khi bi timeout doc, kiem tra ket noi bang cach ping, neu ping OK thi tiep tuc, neu loi thi break de reconnect
                    try:
                        await r.ping()
                        continue
                    except Exception:
                        break
                except (redis.exceptions.ConnectionError, redis.exceptions.RedisError):
                    break

                if not msg:
                    continue
                _, data_str = msg
                try:
                    signal = json.loads(data_str)
                except Exception:
                    continue

                await r.lpush("WEB_SIGNALS", data_str)
                await r.lpush("WEB_SIGNALS_RECORD", data_str)
                await r.ltrim("WEB_SIGNALS", 0, 29)
                await r.ltrim("WEB_SIGNALS_RECORD", 0, 99)

                if not BOT_GLOBAL_AUTO or BOT_KILL_SWITCH:
                    continue

                final = signal.get("final", "WAIT")
                conf  = float(signal.get("confidence", 0))
                if final == "WAIT":
                    continue

                db = SessionLocal()
                try:
                    users = (db.query(User)
                             .filter(User.is_active == True, User.auto_trade == True,
                                     User.capital >= MIN_CAPITAL_TO_TRADE).all())
                except Exception:
                    users = []
                finally:
                    db.close()

                eligible = [u for u in users if conf >= (u.min_confidence or 68)]
                if not eligible:
                    log.info("Khong co user du dieu kien cho signal %.1f%%", conf)
                    continue

                tasks = [asyncio.to_thread(_execute_for_user, u, signal) for u in eligible]
                await asyncio.gather(*tasks, return_exceptions=True)

        except (ConnectionRefusedError, OSError) as e:
            retry += 1
            await asyncio.sleep(min(5 * retry, 60))
        except Exception as e:
            retry += 1
            log.error("Worker error: %s", e)
            await asyncio.sleep(min(5 * retry, 60))
        finally:
            if r:
                try:
                    await r.aclose()
                except Exception:
                    pass


def _execute_for_user(user: User, signal: dict):
    try:
        sym       = signal.get("symbol", "")
        direction = signal.get("final", "WAIT")
        if not sym or direction == "WAIT":
            return

        pending_key = f"PENDING:{user.telegram_id}:{sym}"
        if redis_client:
            try:
                if redis_client.get(pending_key):
                    log.info("Cooldown %s %s - skip", user.telegram_id, sym)
                    return
            except Exception:
                pass

        with _POS_LOCK:
            already = [p for p in LIVE_POSITIONS
                       if str(p.get("user_id")) == user.telegram_id and p.get("symbol") == sym]
        if already:
            log.info("Da co vi the %s (user %s) - bo qua", sym, user.telegram_id)
            return

        try:
            bx_live = get_bx(user)
            live    = bx_live.get_open_positions()
            if any(p.get("symbol") == sym for p in live):
                log.info("BingX xac nhan da co vi the %s - bo qua", sym)
                return
        except Exception as e:
            log.warning("BingX realtime check: %s - tiep tuc", e)

        with _POS_LOCK:
            total_pos = sum(1 for p in LIVE_POSITIONS if str(p.get("user_id")) == user.telegram_id)
        if total_pos >= (user.max_positions or 2):
            log.info("Max positions %d da dat (user %s)", user.max_positions, user.telegram_id)
            return

        entry = float(signal["plan"]["entry"])
        sl    = float(signal["plan"]["sl"])
        tp1   = float(signal["plan"]["tp1"])
        tp2   = float(signal["plan"].get("tp2", 0))
        if tp2 <= 0:
            if direction == "LONG":
                tp2 = round(tp1 + abs(tp1 - entry), 4)
            else:
                tp2 = round(tp1 - abs(tp1 - entry), 4)

        sl_pct = abs(entry - sl) / entry
        if sl_pct < 0.001:
            log.warning("SL qua gan entry (%.4f%%) - bo qua", sl_pct * 100)
            return

        risk_amt = user.capital * (user.max_risk_pct / 100)
        qty      = round(risk_amt / (entry * sl_pct), 4)
        if qty <= 0:
            return

        bx   = get_bx(user)
        side = "BUY" if direction == "LONG" else "SELL"
        bx.set_leverage(sym, leverage=user.leverage)
        bx.cancel_all_orders(sym)
        res = bx.place_order(sym, side, qty, sl, tp2)

        if res.get("ok"):
            if redis_client:
                try:
                    redis_client.setex(pending_key, 60, "1")
                except Exception:
                    pass

            log.info("OK %s: %s %s qty=%.4f lev=%dx", user.telegram_id, direction, sym, qty, user.leverage)
            _tg_send(
                REGISTER_TOKEN, user.telegram_id,
                f"🚨 <b>LỆNH MỚI: {sym}</b>\n"
                f"📈 {direction} | Conf: {signal.get('confidence',0):.1f}%\n"
                f"💰 Qty: {qty:.4f} | Lev: {user.leverage}x\n"
                f"🛑 SL: <code>\${sl:.4f}</code> | Risk: \${risk_amt:.2f}\n"
                f"🎯 TP1: <code>\${tp1:.4f}</code> → chốt 50% + SL → Entry\n"
                f"🏆 TP2: <code>\${tp2:.4f}</code> → đích 50% còn lại")
        else:
            log.error("BingX loi %s: %s", user.telegram_id, res.get("msg"))

    except Exception as e:
        log.error("_execute_for_user %s: %s", user.telegram_id, e)


def run_trade_worker():
    asyncio.run(_trade_worker_async())


def run_signal_bot():
    try:
        SignalBot().start()
    except Exception as e:
        log.error("SignalBot crash: %s", e)


def _register_telegram_webhook():
    if REGISTER_TOKEN and RENDER_URL:
        try:
            url = f"{TG_BASE}/bot{REGISTER_TOKEN}/setWebhook"
            webhook_url = f"{RENDER_URL}/telegram/webhook"
            r = _req.get(url, params={"url": webhook_url}, timeout=10)
            if r.status_code == 200:
                log.info("✅ Auto register Telegram Webhook success: %s", webhook_url)
            else:
                log.warning("⚠️ Failed to register Telegram Webhook: %s", r.text)
        except Exception as e:
            log.warning("⚠️ Error registering Telegram Webhook: %s", e)


@app.on_event("startup")
async def startup_event():
    threading.Thread(target=run_signal_bot,         daemon=True, name="signal-bot").start()
    threading.Thread(target=run_trade_worker,       daemon=True, name="trade-worker").start()
    threading.Thread(target=sync_bingx_positions,   daemon=True, name="pos-sync").start()
    threading.Thread(target=_tp1_monitor,           daemon=True, name="tp1-monitor").start()
    threading.Thread(target=_schedule_weekly_report, daemon=True, name="report-bot").start()
    threading.Thread(target=_register_telegram_webhook, daemon=True, name="webhook-register").start()
    threading.Thread(target=lambda: [time.sleep(600) or gc.collect() for _ in iter(int, 1)],
                     daemon=True, name="gc").start()
    log.info("Tat ca threads khoi dong")


# ══════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════
@app.get("/")
def health():
    return {"status": "online", "version": "v6.1",
            "tiers": {t: c["label"] for t, c in TIER_CONFIG.items()}}


# ══════════════════════════════════════════════════════════════════
# TELEGRAM WEBHOOKS
# ══════════════════════════════════════════════════════════════════
@app.post("/telegram/webhook")
async def tg_webhook_bot2(request: Request):
    try:
        data = await request.json()
        msg  = data.get("message", {})
        if not msg:
            return {"status": "ok"}
        chat_id = str(msg["chat"]["id"])
        text    = msg.get("text", "")

        if text == "/start":
            miniapp_url = (RENDER_URL or "https://auto-trade-v6.onrender.com") + "/miniapp/connect"
            _tg_send_inline(
                REGISTER_TOKEN, chat_id,
                "👋 <b>Chào mừng đến với SignalBot v6.1!</b>\n\n"
                "Để bắt đầu copy trade tự động, kết nối BingX API của bạn.\n"
                "⚠️ Không cấp quyền <b>Rút Tiền</b> cho API Key!",
                {"inline_keyboard": [[{"text": "🔗 Kết Nối BingX API",
                                       "web_app": {"url": miniapp_url}}]]})

        elif text == "/status":
            db = SessionLocal()
            u  = db.query(User).filter(User.telegram_id == chat_id).first()
            db.close()
            if not u:
                _tg_send(REGISTER_TOKEN, chat_id, "❌ Bạn chưa đăng ký. Gõ /start để bắt đầu.")
            else:
                cfg = TIER_CONFIG.get(u.tier, TIER_CONFIG["TIER1"])
                _tg_send(
                    REGISTER_TOKEN, chat_id,
                    f"📊 <b>Tài Khoản Của Bạn</b>\n\n"
                    f"💰 Vốn: <b>\${u.capital:.2f}</b>\n"
                    f"🏷 Tier: <b>{cfg['label']}</b>\n"
                    f"🎯 Min Confidence: <b>{u.min_confidence}%</b>\n"
                    f"⚡ Leverage: <b>{u.leverage}x</b>\n"
                    f"📈 Risk/Lệnh: <b>{u.max_risk_pct}%</b>\n"
                    f"🔄 Auto-trade: <b>{'BẬT' if u.auto_trade else 'TẮT'}</b>\n"
                    f"📊 Total PnL: <b>\${u.total_pnl:+.2f}</b>")

        elif text == "/dashboard":
            dash_url = (RENDER_URL or "") + f"/my-dashboard?uid={chat_id}"
            _tg_send_inline(
                REGISTER_TOKEN, chat_id, "📊 Mở Dashboard cá nhân của bạn:",
                {"inline_keyboard": [[{"text": "📊 Dashboard Của Tôi",
                                       "web_app": {"url": dash_url}}]]})

        elif text == "/report":
            threading.Thread(target=_send_daily_report, daemon=True).start()
            _tg_send(REGISTER_TOKEN, chat_id, "📊 Đang tạo báo cáo ngày, vui lòng chờ...")
            
        elif text.startswith("/close "):
            symbol = text.split(" ", 1)[1].strip().upper()
            _handle_user_close(chat_id, symbol)

    except Exception as e:
        log.error("tg_webhook_bot2: %s", e)
    return {"status": "ok"}


def _handle_user_close(telegram_id: str, symbol: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            _tg_send(REGISTER_TOKEN, telegram_id, "❌ Tài khoản không tồn tại.")
            return

        bx = get_bx(user)

        with _POS_LOCK:
            user_pos = [p for p in LIVE_POSITIONS
                        if str(p.get("user_id")) == telegram_id
                        and p.get("symbol") == symbol]

        if not user_pos:
            try:
                live = bx.get_open_positions()
                for lp in live:
                    if lp.get("symbol") == symbol:
                        user_pos = [lp]
                        break
            except Exception as e:
                log.warning("get_open_positions for close: %s", e)

        if not user_pos:
            _tg_send(REGISTER_TOKEN, telegram_id, f"❌ Không tìm thấy lệnh <b>{symbol}</b> đang mở.")
            return

        p   = user_pos[0]
        qty = float(p.get("qty", 0))
        direction = p.get("direction", "LONG")

        if qty <= 0:
            try:
                live = bx.get_open_positions()
                for lp in live:
                    if lp.get("symbol") == symbol:
                        qty = float(lp.get("qty", 0))
                        direction = lp.get("direction", direction)
                        break
            except Exception:
                pass

        if qty <= 0:
            _tg_send(REGISTER_TOKEN, telegram_id, f"❌ Không xác định được khối lượng lệnh {symbol}.")
            return

        res = bx.close_position(symbol, qty, direction)
        if res.get("ok"):
            pnl     = float(p.get("pnl", 0))
            pct     = float(p.get("pnl_pct", 0))
            sign    = "+" if pnl >= 0 else ""
            _tg_send(REGISTER_TOKEN, telegram_id,
                     f"✅ <b>Đã đóng lệnh {symbol}!</b>\n"
                     f"📈 {direction} | Qty: {qty:.4f}\n"
                     f"💰 PnL: <b>{sign}\${pnl:.2f} ({sign}{pct:.2f}%)</b>")
            if redis_client:
                try:
                    redis_client.delete(f"TP1_DONE:{telegram_id}:{symbol}:{direction}")
                    redis_client.delete(f"PENDING:{telegram_id}:{symbol}")
                except Exception:
                    pass
        else:
            err_msg = res.get("msg", "Unknown error")
            _tg_send(REGISTER_TOKEN, telegram_id,
                     f"❌ Lỗi đóng lệnh {symbol}:\n<code>{err_msg}</code>")
            log.error("_handle_user_close %s %s: %s", telegram_id, symbol, err_msg)

    except Exception as e:
        _tg_send(REGISTER_TOKEN, telegram_id, f"❌ Lỗi hệ thống: {str(e)[:200]}")
        log.error("_handle_user_close exception %s: %s", telegram_id, e)
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════
# ĐĂNG KÝ USER
# ══════════════════════════════════════════════════════════════════

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class UserRegister(BaseModel):
    telegram_id: str
    api_key:     str
    api_secret:  str


@app.post("/api/users/register")
def register_user(data: UserRegister, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.telegram_id == data.telegram_id).first()

    try:
        bx      = BingXExchange(data.api_key, data.api_secret)
        capital = bx.get_balance()
        if capital <= 0:
            price = bx.get_latest_price("BTC-USDT")
            if price <= 0:
                raise HTTPException(400, "API Key không hợp lệ hoặc không kết nối được BingX")
            capital = float(os.getenv("DEFAULT_CAPITAL", "100"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Lỗi kết nối BingX: {str(e)[:100]}")

    tier = get_tier(capital)
    if not tier:
        raise HTTPException(400, f"Số dư \${capital:.2f} thấp hơn tối thiểu \${MIN_CAPITAL_TO_TRADE:.0f}.")

    if existing:
        existing.api_key              = data.api_key
        existing.api_secret_encrypted = encrypt_api_secret(data.api_secret)
        existing.is_active            = True
        existing.is_locked            = False
        existing.auto_trade           = True
        _update_user_balance_and_tier(existing, capital, db)
        db.commit()
        cfg = TIER_CONFIG[existing.tier]
        return {"status": "updated", "tier": existing.tier, "label": cfg["label"],
                "capital": f"\${capital:.2f}", "min_confidence": cfg["min_confidence"],
                "target_monthly": cfg["target_monthly"]}

    new_user = User(
        telegram_id=data.telegram_id, exchange="BINGX",
        api_key=data.api_key, api_secret_encrypted=encrypt_api_secret(data.api_secret),
        capital=round(capital, 2), auto_trade=True,
        registered_at=datetime.utcnow(), last_balance_update=datetime.utcnow())
    apply_tier(new_user, tier)
    db.add(new_user)
    db.commit()

    cfg = TIER_CONFIG[tier]
    notify_admin(
        f"🆕 <b>User mới đăng ký!</b>\n"
        f"👤 UID: <code>{data.telegram_id}</code>\n"
        f"💰 Vốn: \${capital:.2f} | {cfg['label']}\n"
        f"🎯 Conf: {cfg['min_confidence']}% | Risk: {cfg['max_risk_pct']}%")

    return {"status": "success", "tier": tier, "label": cfg["label"],
            "capital": f"\${capital:.2f}", "min_confidence": cfg["min_confidence"],
            "leverage": cfg["leverage"], "target_monthly": cfg["target_monthly"],
            "msg": f"Đăng ký thành công! Tier: {cfg['label']}"}


# ══════════════════════════════════════════════════════════════════
# MARKET DEPTH API
# ══════════════════════════════════════════════════════════════════
@app.get("/api/market-depth")
def get_market_depth(symbol: str = Query(default="BTCUSDT")):
    from analyzer.fetcher import CryptoFetcher
    fetcher = CryptoFetcher()
    
    symbol = symbol.upper().strip()
    if not symbol:
        return {"success": False, "error": "Invalid symbol"}
        
    try:
        price = fetcher.price(symbol)
        if not price or price <= 0:
            k = fetcher.klines(symbol, "1m")
            if k and len(k.get("close", [])) > 0:
                price = k["close"][-1]
            else:
                price = 0.0
                
        ob = fetcher.order_book(symbol, depth=10)
        liq = fetcher.liquidation_levels(symbol, price if price > 0 else 1.0)
        
        return {
            "success": True,
            "symbol": symbol,
            "price": price,
            "orderbook": ob,
            "liquidation": liq
        }
    except Exception as e:
        log.error("API /api/market-depth error for %s: %s", symbol, e)
        return {"success": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════
# STATE API
# ══════════════════════════════════════════════════════════════════
@app.get("/api/state")
def get_state(request: Request, db: Session = Depends(get_db), uid: str = Query(default="")):
    if uid:
        user = db.query(User).filter(User.telegram_id == uid).first()
        if not user:
            return {"error": "User not found"}
        with _POS_LOCK:
            positions = [p for p in LIVE_POSITIONS if str(p.get("user_id")) == uid]
        cfg = TIER_CONFIG.get(user.tier, TIER_CONFIG["TIER1"])
        return {
            "auto_trade": user.auto_trade, "kill_switch": BOT_KILL_SWITCH,
            "tier": user.tier, "tier_label": cfg["label"],
            "min_confidence": user.min_confidence,
            "stats": {"equity": user.capital, "total_return": 0,
                     "daily_pnl_pct": 0, "total_pnl": user.total_pnl or 0},
            "positions": positions, "signals": _get_signals(),
        }

    users = db.query(User).filter(User.is_active == True).all()
    tier_summary = {}
    for t, cfg in TIER_CONFIG.items():
        tier_users = [u for u in users if u.tier == t]
        tier_summary[t] = {
            "label": cfg["label"], "count": len(tier_users),
            "capital": sum(u.capital or 0 for u in tier_users),
            "min_confidence": cfg["min_confidence"],
        }
    with _POS_LOCK:
        positions = list(LIVE_POSITIONS)

    return {
        "auto_trade": BOT_GLOBAL_AUTO, "kill_switch": BOT_KILL_SWITCH,
        "stats": {
            "equity": sum(u.capital or 0 for u in users), "total_users": len(users),
            "total_return": 0, "daily_pnl_pct": 0, "win_rate": 0,
            "profit_factor": 0, "drawdown_pct": 0,
        },
        "tier_summary": tier_summary, "positions": positions,
        "signals": _get_signals(), "risk_config": _redis_get("GLOBAL:RISK_CONFIG", {}),
    }


def _get_signals():
    if not redis_client:
        return []
    try:
        raws = redis_client.lrange("WEB_SIGNALS", 0, 19)
        result = []
        for raw in raws:
            d = json.loads(raw)
            result.append({"symbol": d.get("symbol"), "final": d.get("final"),
                           "confidence": d.get("confidence", 0),
                           "timestamp": d.get("timestamp", ""),
                           "asset_type": d.get("asset_type", "CRYPTO")})
        return result
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
# ADMIN COMMANDS
# ══════════════════════════════════════════════════════════════════
@app.post("/api/cmd")
async def handle_cmd(request: Request, token: str = Query(default="")):
    global BOT_GLOBAL_AUTO, BOT_KILL_SWITCH
    if token != ADMIN_SECRET:
        raise HTTPException(401, "Unauthorized")

    try:
        cmd = await request.json()
        action = cmd.get("action", "")

        if action == "update_risk":
            cfg = cmd.get("config", {})
            _redis_set("GLOBAL:RISK_CONFIG", cfg)
            return {"ok": True, "msg": "Da luu cau hinh risk"}

        if action == "toggle_auto":
            BOT_GLOBAL_AUTO = bool(cmd.get("enabled", not BOT_GLOBAL_AUTO))
            db = SessionLocal()
            try:
                db.query(User).filter(User.is_active == True).update(
                    {"auto_trade": BOT_GLOBAL_AUTO}, synchronize_session=False)
                db.commit()
            finally:
                db.close()
            return {"ok": True, "msg": "Auto-trade " + ("BAT" if BOT_GLOBAL_AUTO else "TAT")}

        if action == "kill_switch":
            BOT_KILL_SWITCH = bool(cmd.get("enabled", not BOT_KILL_SWITCH))
            if BOT_KILL_SWITCH:
                threading.Thread(target=_close_all_positions, daemon=True).start()
            return {"ok": True, "msg": "Kill Switch " + ("BAT - dang dong tat ca" if BOT_KILL_SWITCH else "TAT")}

        if action == "close":
            sym = cmd.get("symbol", "").upper()
            if sym:
                threading.Thread(target=_close_symbol, args=(sym,), daemon=True).start()
            return {"ok": True, "msg": f"Dang dong {sym}"}

        if action == "update_user":
            tid  = cmd.get("telegram_id", "")
            data = cmd.get("data", {})
            db   = SessionLocal()
            try:
                u = db.query(User).filter(User.telegram_id == tid).first()
                if not u:
                    return {"ok": False, "msg": "User khong ton tai"}
                allowed = {"max_risk_pct", "max_positions", "leverage",
                          "capital", "auto_trade", "min_confidence", "tier"}
                for k, v in data.items():
                    if k in allowed:
                        setattr(u, k, v)
                db.commit()
            finally:
                db.close()
            return {"ok": True, "msg": f"Cap nhat {tid}"}

        if action == "send_report":
            rtype = cmd.get("type", "daily")
            if rtype == "weekly":
                threading.Thread(target=_send_weekly_report, daemon=True).start()
                return {"ok": True, "msg": "📊 Đang gửi báo cáo TUẦN..."}
            else:
                threading.Thread(target=_send_daily_report, daemon=True).start()
                return {"ok": True, "msg": "📊 Đang gửi báo cáo NGÀY..."}

        if action == "cleanup":
            threading.Thread(target=_cleanup_inactive_users, daemon=True).start()
            return {"ok": True, "msg": "Dang don DB..."}

        if action == "trigger_signal":
            signal_data = cmd.get("signal", {})
            if not signal_data or not signal_data.get("symbol"):
                return {"ok": False, "msg": "Tin hieu thieu thong tin symbol"}
            if redis_client:
                redis_client.rpush("TRADE_SIGNALS", json.dumps(signal_data))
                return {"ok": True, "msg": f"Đã gửi tín hiệu {signal_data.get('symbol')} ({signal_data.get('final')}) vào hàng đợi Redis!"}
            else:
                return {"ok": False, "msg": "Lỗi: Redis không kết nối!"}

        return {"ok": False, "msg": f"Khong ho tro: {action}"}

    except Exception as e:
        return {"ok": False, "msg": str(e)}


@app.post("/api/user/close")
async def user_close_position(request: Request):
    try:
        body   = await request.json()
        uid    = body.get("user_id", "").strip()
        symbol = body.get("symbol", "").upper().strip()
        if not uid or not symbol:
            return {"ok": False, "msg": "Thieu user_id hoac symbol"}
        _handle_user_close(uid, symbol)
        return {"ok": True, "msg": f"Dang dong {symbol}..."}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ══════════════════════════════════════════════════════════════════
# USERS API
# ══════════════════════════════════════════════════════════════════
@app.get("/api/users")
def list_users(token: str = Query(default=""), db: Session = Depends(get_db)):
    if token != ADMIN_SECRET:
        raise HTTPException(401, "Unauthorized")
    users = db.query(User).filter(User.is_active == True).all()
    return [{
        "telegram_id": u.telegram_id, "tier": u.tier, "capital": u.capital,
        "min_confidence": u.min_confidence, "max_risk_pct": u.max_risk_pct,
        "max_positions": u.max_positions, "leverage": u.leverage,
        "auto_trade": u.auto_trade, "total_pnl": u.total_pnl or 0,
        "registered_at": u.registered_at.strftime("%d/%m/%Y") if u.registered_at else "",
    } for u in users]


@app.put("/api/users/{telegram_id}")
def update_user(telegram_id: str, body: dict, token: str = Query(default=""),
                db: Session = Depends(get_db)):
    if token != ADMIN_SECRET:
        raise HTTPException(401, "Unauthorized")
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        raise HTTPException(404, "User khong ton tai")
    allowed = {"min_confidence", "max_risk_pct", "max_positions",
              "leverage", "auto_trade", "capital", "tier"}
    for k, v in body.items():
        if k in allowed:
            setattr(user, k, v)
    db.commit()
    return {"ok": True}


@app.delete("/api/users/{telegram_id}")
def delete_user(telegram_id: str, token: str = Query(default=""), db: Session = Depends(get_db)):
    if token != ADMIN_SECRET:
        raise HTTPException(401, "Unauthorized")
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        raise HTTPException(404, "User khong ton tai")
    db.delete(user)
    db.commit()
    return {"ok": True, "msg": f"Da xoa {telegram_id}"}


def _close_all_positions():
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()
    finally:
        db.close()

    with _POS_LOCK:
        positions = list(LIVE_POSITIONS)

    for user in users:
        try:
            bx = get_bx(user)
            user_pos = [p for p in positions
                        if str(p.get("user_id")) == user.telegram_id]
            for p in user_pos:
                qty = float(p.get("qty", 0))
                if qty <= 0:
                    live = bx.get_open_positions()
                    for lp in live:
                        if lp.get("symbol") == p["symbol"]:
                            qty = float(lp.get("qty", 0))
                            break
                if qty > 0:
                    res = bx.close_position(p["symbol"], qty, p["direction"])
                    if not res.get("ok"):
                        log.error("close_all %s %s: %s",
                                  user.telegram_id, p["symbol"], res.get("msg"))
                time.sleep(0.2)
        except Exception as e:
            log.error("close_all user %s: %s", user.telegram_id, e)


def _close_symbol(symbol: str):
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()
    finally:
        db.close()

    with _POS_LOCK:
        pos_list = [p for p in LIVE_POSITIONS if p.get("symbol") == symbol]

    for user in users:
        try:
            bx = get_bx(user)
            user_pos = [p for p in pos_list
                        if str(p.get("user_id")) == user.telegram_id]
            for p in user_pos:
                qty = float(p.get("qty", 0))
                if qty <= 0:
                    live = bx.get_open_positions()
                    for lp in live:
                        if lp.get("symbol") == symbol:
                            qty = float(lp.get("qty", 0))
                            break
                if qty > 0:
                    bx.close_position(symbol, qty, p["direction"])
        except Exception as e:
            log.error("close_symbol %s user %s: %s", symbol, user.telegram_id, e)


# ══════════════════════════════════════════════════════════════════
# ADMIN DASHBOARD GATE & HTML
# ══════════════════════════════════════════════════════════════════

ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SIGNALBOT v6.1 - Admin Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #030611;
            color: #e2e8f0;
        }
        .font-mono {
            font-family: 'JetBrains Mono', monospace;
        }
        .scrollbar-thin::-webkit-scrollbar {
            width: 5px;
            height: 5px;
        }
        .scrollbar-thin::-webkit-scrollbar-track {
            background: #040811;
        }
        .scrollbar-thin::-webkit-scrollbar-thumb {
            background: #1b263e;
            border-radius: 4px;
        }
        .badge-active {
            background-color: rgba(16, 185, 129, 0.1);
            color: #10b981;
            border: 1px solid rgba(16, 185, 129, 0.2);
        }
        .badge-inactive {
            background-color: rgba(239, 68, 68, 0.1);
            color: #ef4444;
            border: 1px solid rgba(239, 68, 68, 0.2);
        }
    </style>
</head>
<body class="min-h-screen flex flex-col antialiased">

    <!-- LOGIN SCREEN -->
    <div id="login-screen" class="hidden fixed inset-0 z-50 flex items-center justify-center bg-[#030611] px-4">
        <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-8 max-w-md w-full shadow-2xl">
            <div class="text-center mb-6">
                <h1 class="text-2xl font-extrabold tracking-wider text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-cyan-400">
                    SIGNALBOT v6.1
                </h1>
                <p class="text-xs text-[#718096] mt-2 font-medium">Đăng nhập quyền quản trị tối cao</p>
            </div>
            
            <form id="login-form" class="space-y-4">
                <div>
                    <label class="text-xs text-[#718096] font-bold uppercase tracking-wider block mb-1">Mật khẩu Admin</label>
                    <input type="password" id="admin-password" placeholder="Nhập mã bảo mật..." required
                           class="w-full bg-[#040811] border border-[#1b263e] rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-emerald-500 font-mono text-[#e2e8f0]">
                </div>
                <button type="submit" 
                        class="w-full py-3 rounded-lg text-sm font-black bg-gradient-to-r from-emerald-500 to-cyan-500 text-black hover:opacity-90 transition-all shadow-[0_0_15px_rgba(16,185,129,0.15)]">
                    XÁC THỰC HỆ THỐNG
                </button>
                <p id="login-error" class="text-red-400 text-xs font-semibold text-center mt-2 hidden">⚠️ Sai mật khẩu hoặc lỗi kết nối!</p>
            </form>
        </div>
    </div>

    <!-- MAIN DASHBOARD CONTENT -->
    <div id="dashboard-content" class="hidden flex-1 flex flex-col">
        <!-- Header -->
        <header class="border-b border-[#1b263e] bg-[#080d1a] px-6 py-4 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 shrink-0 shadow-lg">
            <div>
                <div class="flex items-center gap-3">
                    <h1 class="text-xl font-extrabold tracking-wider text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 via-mint-300 to-cyan-400">
                        SIGNAL<span class="text-[#05f38c]">BOT</span> v6.1
                    </h1>
                    <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1.5">
                        <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block animate-pulse"></span>
                        LIVE DASHBOARD
                    </span>
                    <button onclick="logout()" class="text-xs text-[#718096] hover:text-red-400 font-bold ml-4 border-l border-[#1b263e] pl-4">👉 ĐĂNG XUẤT</button>
                </div>
                <p class="text-xs text-[#718096] mt-1 font-medium">Bảng điều khiển quản trị viên và cấu hình thanh khoản orderbook theo thời gian thực</p>
            </div>

            <div class="flex items-center gap-4 self-stretch md:self-auto justify-between md:justify-end font-mono text-xs">
                <div>
                    <span class="text-[#718096]">AUTO SYSTEM: </span>
                    <span id="header-auto-badge" class="font-bold">ĐANG TẢI...</span>
                </div>
                <div class="h-6 w-px bg-[#1b263e]"></div>
                <div>
                    <span class="text-red-400 font-bold">KILL SWITCH: </span>
                    <span id="header-kill-badge" class="font-bold">ĐANG TẢI...</span>
                </div>
            </div>
        </header>

        <!-- Workspace Grid -->
        <main class="flex-1 p-6 w-full max-w-[1700px] mx-auto grid grid-cols-1 xl:grid-cols-12 gap-6 overflow-hidden">
            
            <!-- LEFT COLUMN: System Controls, Config & Users (4 cols) -->
            <div class="xl:col-span-4 flex flex-col gap-6">
                <!-- System Config -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-lg">
                    <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                        <i data-lucide="settings" class="w-4 h-4 text-emerald-400"></i> Bảng lệnh hệ thống
                    </h2>

                    <div class="grid grid-cols-2 gap-3">
                        <button onclick="toggleGlobalAuto()" class="py-2.5 px-3 rounded-lg text-xs font-black border border-[#1b263e] bg-[#080d1a] text-emerald-400 hover:bg-emerald-950/20 hover:border-emerald-500/40 transition-all flex items-center justify-center gap-2">
                            <i data-lucide="play" class="w-3.5 h-3.5"></i> AUTO-TRADE
                        </button>
                        <button onclick="toggleKillSwitch()" class="py-2.5 px-3 rounded-lg text-xs font-black border border-red-500/20 bg-red-950/10 text-red-400 hover:bg-red-900/30 hover:border-red-500/40 transition-all flex items-center justify-center gap-2">
                            <i data-lucide="shield-alert" class="w-3.5 h-3.5 animate-pulse"></i> KILL SWITCH
                        </button>
                    </div>

                    <div class="border-t border-[#1b263e]/40 my-1"></div>

                    <div class="grid grid-cols-3 gap-2">
                        <button onclick="triggerAction('send_report', {type: 'daily'})" class="py-2 px-1 text-center bg-[#070b16] hover:bg-[#141d2e] rounded border border-[#1b263e] text-[10px] font-black text-[#e2e8f0] transition-all">
                            📊 BÁO CÁO NGÀY
                        </button>
                        <button onclick="triggerAction('send_report', {type: 'weekly'})" class="py-2 px-1 text-center bg-[#070b16] hover:bg-[#141d2e] rounded border border-[#1b263e] text-[10px] font-black text-[#e2e8f0] transition-all">
                            📊 BÁO CÁO TUẦN
                        </button>
                        <button onclick="triggerAction('cleanup')" class="py-2 px-1 text-center bg-[#070b16] hover:bg-[#141d2e] rounded border border-[#1b263e] text-[10px] font-black text-[#e2e8f0] transition-all">
                            🧹 DỌN DẸP DB
                        </button>
                    </div>
                </div>

                <!-- Add/Update User Form -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-lg">
                    <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                        <i data-lucide="user-plus" class="w-4 h-4 text-emerald-400"></i> Đăng ký / Sửa User
                    </h2>

                    <form id="user-form" class="space-y-3">
                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Telegram UID</label>
                                <input type="text" id="form-user-id" required placeholder="6286755..."
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-mono text-[#e2e8f0]">
                            </div>
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Vốn (Capital)</label>
                                <input type="number" id="form-user-capital" required placeholder="500.0"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-mono text-[#e2e8f0]">
                            </div>
                        </div>

                        <div class="grid grid-cols-3 gap-2">
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Min Conf (%)</label>
                                <input type="number" id="form-user-conf" value="70"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-mono text-[#e2e8f0]">
                            </div>
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Risk / Lệnh (%)</label>
                                <input type="number" step="0.1" id="form-user-risk" value="1.5"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-mono text-[#e2e8f0]">
                            </div>
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Leverage</label>
                                <input type="number" id="form-user-lev" value="5"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-mono text-[#e2e8f0]">
                            </div>
                        </div>

                        <button type="submit" class="w-full py-2 bg-emerald-500 hover:bg-emerald-600 text-black font-black text-xs rounded transition-all">
                            💾 LƯU THÔNG TIN USER
                        </button>
                    </form>
                </div>

                <!-- Users List -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-3 shadow-lg flex-1 min-h-[250px] overflow-hidden">
                    <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2 mb-2">
                        <i data-lucide="users" class="w-4 h-4 text-emerald-400"></i> Người dùng copy trade (<span id="user-count">0</span>)
                    </h2>

                    <div class="overflow-y-auto max-h-[350px] scrollbar-thin flex flex-col gap-2" id="users-container">
                        <!-- Users render dynamically -->
                    </div>
                </div>
            </div>

            <!-- MIDDLE COLUMN: Orderbook & Liquidity Real-time View (5 cols) -->
            <div class="xl:col-span-5 flex flex-col gap-6">
                <!-- Dynamic Orderbook & Depth Map -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-lg">
                    <div class="flex justify-between items-center">
                        <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                            <i data-lucide="book-open" class="w-4 h-4 text-emerald-400"></i> Phân tích Sổ Lệnh L2 (Orderbook)
                        </h2>
                        <span id="orderbook-imb" class="font-mono text-xs font-black">--</span>
                    </div>

                    <div class="grid grid-cols-2 gap-4 text-xs font-mono bg-[#040811] p-3 rounded-lg border border-[#141d2e] relative">
                        <!-- Asks Column (Sells) -->
                        <div class="flex flex-col gap-1">
                            <span class="text-red-400 font-bold block border-b border-[#1b263e]/40 pb-1 mb-1 text-center">ASK (SELL WALLS)</span>
                            <div id="asks-container" class="flex flex-col gap-1 h-[140px] overflow-y-auto scrollbar-thin">
                                <!-- Asks map here -->
                            </div>
                        </div>

                        <!-- Bids Column (Buys) -->
                        <div class="flex flex-col gap-1">
                            <span class="text-emerald-400 font-bold block border-b border-[#1b263e]/40 pb-1 mb-1 text-center">BID (BUY WALLS)</span>
                            <div id="bids-container" class="flex flex-col gap-1 h-[140px] overflow-y-auto scrollbar-thin">
                                <!-- Bids map here -->
                            </div>
                        </div>
                    </div>

                    <div class="grid grid-cols-2 gap-3 text-center">
                        <div class="bg-[#040811] rounded p-2 text-xs border border-[#141d2e]">
                            <span class="text-[#718096] text-[10px] uppercase font-bold block">Tường Mua Lớn Nhất</span>
                            <span id="buy-wall-val" class="text-emerald-400 font-bold block mt-0.5">--</span>
                        </div>
                        <div class="bg-[#040811] rounded p-2 text-xs border border-[#141d2e]">
                            <span class="text-[#718096] text-[10px] uppercase font-bold block">Tường Bán Lớn Nhất</span>
                            <span id="sell-wall-val" class="text-red-400 font-bold block mt-0.5">--</span>
                        </div>
                    </div>
                </div>

                <!-- Liquidation Map & Cascade Risk Indicator -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-lg">
                    <div class="flex justify-between items-center">
                        <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                            <i data-lucide="layers" class="w-4 h-4 text-emerald-400"></i> Bản đồ thanh lý đòn bẩy
                        </h2>
                        <span id="cascade-risk-badge" class="px-2 py-0.5 rounded text-[10px] font-black">ĐANG QUÉT</span>
                    </div>

                    <div class="grid grid-cols-2 gap-4 text-xs font-mono">
                        <!-- Long Liquidation Levels -->
                        <div class="bg-[#040811] border border-[#141d2e] rounded-lg p-3">
                            <span class="text-emerald-400 font-bold block border-b border-[#1b263e]/30 pb-1 mb-2">Thanh Lý LONG</span>
                            <div id="long-liqs" class="space-y-1.5 text-[11px]">
                                <!-- Long levels -->
                            </div>
                        </div>

                        <!-- Short Liquidation Levels -->
                        <div class="bg-[#040811] border border-[#141d2e] rounded-lg p-3">
                            <span class="text-red-400 font-bold block border-b border-[#1b263e]/30 pb-1 mb-2">Thanh Lý SHORT</span>
                            <div id="short-liqs" class="space-y-1.5 text-[11px]">
                                <!-- Short levels -->
                            </div>
                        </div>
                    </div>

                    <div class="bg-[#040811] border border-[#141d2e] rounded-lg p-3 text-xs flex justify-between font-mono">
                        <div>
                            <span class="text-[#718096] text-[10px] uppercase block">dominant side</span>
                            <span id="dominant-side-val" class="font-bold">--</span>
                        </div>
                        <div class="text-right">
                            <span class="text-[#718096] text-[10px] uppercase block">spread thị trường</span>
                            <span id="spread-pct-val" class="font-bold">--</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- RIGHT COLUMN: Signal Commander & Open Positions (3 cols) -->
            <div class="xl:col-span-3 flex flex-col gap-6">
                <!-- Signal Commander Form -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-4 shadow-lg">
                    <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2">
                        <i data-lucide="zap" class="w-4 h-4 text-emerald-400"></i> Bộ Phát Tín Hiệu (Redis Command)
                    </h2>

                    <form id="signal-form" class="space-y-3 font-mono text-xs">
                        <div>
                            <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Cặp Giao Dịch</label>
                            <div class="grid grid-cols-3 gap-2">
                                <select id="sig-symbol-select" onchange="document.getElementById('sig-symbol').value = this.value; updateMarketDepth();"
                                        class="col-span-2 bg-[#040811] border border-[#1b263e] rounded p-2 text-xs text-emerald-400 font-bold">
                                    <option value="BTCUSDT" selected>BTCUSDT (Bitcoin)</option>
                                    <option value="ETHUSDT">ETHUSDT (Ethereum)</option>
                                    <option value="BNBUSDT">BNBUSDT (Binance Coin)</option>
                                    <option value="SOLUSDT">SOLUSDT (Solana)</option>
                                    <option value="HYPEUSDT">HYPEUSDT (Hyperliquid)</option>
                                    <option value="XAUUSD">XAUUSD (Gold)</option>
                                    <option value="TSLA">TSLA (Tesla)</option>
                                    <option value="NVDA">NVDA (Nvidia)</option>
                                    <option value="SPY">SPY (S&P 500)</option>
                                    <option value="QQQ">QQQ (Nasdaq 100)</option>
                                    <option value="">Khác (Tùy chọn)...</option>
                                </select>
                                <input type="text" id="sig-symbol" required value="BTCUSDT" oninput="updateMarketDepth();"
                                       class="col-span-1 bg-[#040811] border border-[#1b263e] rounded p-2 text-xs uppercase text-center text-gray-200" placeholder="Symbol">
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Hướng Lệnh</label>
                                <select id="sig-direction" class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs font-bold text-emerald-400">
                                    <option value="LONG" class="text-emerald-400 font-bold">LONG</option>
                                    <option value="SHORT" class="text-red-400 font-bold">SHORT</option>
                                </select>
                            </div>
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Độ Tin Cậy (%)</label>
                                <input type="number" id="sig-confidence" required value="85"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs">
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="text-[10px] text-[#718096] font-bold block uppercase mb-1">Giá Vào Lệnh</label>
                                <input type="number" step="0.01" id="sig-entry" required placeholder="92500"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs">
                            </div>
                            <div>
                                <label class="text-[10px] text-red-400 font-bold block uppercase mb-1">Giá Cắt Lỗ (SL)</label>
                                <input type="number" step="0.01" id="sig-sl" required placeholder="91200"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs text-red-400">
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="text-[10px] text-emerald-400 font-bold block uppercase mb-1">Chốt Lời 1 (TP1)</label>
                                <input type="number" step="0.01" id="sig-tp1" required placeholder="94500"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs text-emerald-400">
                            </div>
                            <div>
                                <label class="text-[10px] text-blue-400 font-bold block uppercase mb-1">Chốt Lời 2 (TP2)</label>
                                <input type="number" step="0.01" id="sig-tp2" required placeholder="96000"
                                       class="w-full bg-[#040811] border border-[#1b263e] rounded p-2 text-xs text-blue-400">
                            </div>
                        </div>

                        <button type="submit" class="w-full py-2.5 bg-gradient-to-r from-emerald-500 to-cyan-500 text-black font-extrabold text-xs rounded transition-all shadow-[0_0_10px_rgba(16,185,129,0.1)]">
                            🚀 PHÁT TÍN HIỆU (EMIT SIGNAL)
                        </button>
                    </form>
                </div>

                <!-- Live Positions -->
                <div class="bg-[#0b1120] border border-[#1b263e] rounded-xl p-5 flex flex-col gap-3 shadow-lg flex-1 min-h-[220px] overflow-hidden">
                    <h2 class="text-sm font-bold text-[#718096] tracking-wider uppercase flex items-center gap-2 mb-2">
                        <i data-lucide="terminal" class="w-4 h-4 text-emerald-400"></i> Lệnh Đang Mở (Real-time)
                    </h2>

                    <div id="positions-container" class="overflow-y-auto max-h-[350px] scrollbar-thin flex flex-col gap-2">
                        <!-- Positions rendered dynamically -->
                    </div>
                </div>
            </div>

        </main>
    </div>

    <!-- SCRIPT FOR DYNAMIC DATA POLLING -->
    <script>
        let adminToken = localStorage.getItem('admin_token') || '';

        document.getElementById('login-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const pass = document.getElementById('admin-password').value;
            // Test validity by list_users call
            try {
                const res = await fetch('/api/users?token=' + encodeURIComponent(pass));
                if (res.status === 200) {
                    adminToken = pass;
                    localStorage.setItem('admin_token', pass);
                    showDashboard();
                } else {
                    showError();
                }
            } catch (err) {
                showError();
            }
        });

        function showError() {
            const err = document.getElementById('login-error');
            err.classList.remove('hidden');
            setTimeout(() => err.classList.add('hidden'), 5000);
        }

        function checkAuth() {
            if (!adminToken) {
                document.getElementById('login-screen').classList.remove('hidden');
                document.getElementById('dashboard-content').classList.add('hidden');
            } else {
                showDashboard();
            }
        }

        function logout() {
            localStorage.removeItem('admin_token');
            adminToken = '';
            checkAuth();
        }

        function showDashboard() {
            document.getElementById('login-screen').classList.add('hidden');
            document.getElementById('dashboard-content').classList.remove('hidden');
            lucide.createIcons();
            fetchData();
            // Start regular intervals
            setInterval(fetchData, 3000);
        }

        async function triggerAction(actionName, extraParams = {}) {
            if (!adminToken) return;
            const payload = { action: actionName, ...extraParams };
            try {
                const res = await fetch('/api/cmd?token=' + encodeURIComponent(adminToken), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const d = await res.json();
                alert(d.msg || (d.ok ? 'Thao tác thành công!' : 'Có lỗi xảy ra!'));
                fetchData();
            } catch (err) {
                alert('Lỗi kết nối API!');
            }
        }

        async function toggleGlobalAuto() {
            if (!adminToken) return;
            try {
                const res = await fetch('/api/state?token=' + encodeURIComponent(adminToken));
                const state = await res.json();
                const nextState = !state.auto_trade;
                const r = await fetch('/api/cmd?token=' + encodeURIComponent(adminToken), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'toggle_auto', enabled: nextState })
                });
                const d = await r.json();
                fetchData();
            } catch(e) {}
        }

        async function toggleKillSwitch() {
            if (!adminToken) return;
            try {
                const res = await fetch('/api/state?token=' + encodeURIComponent(adminToken));
                const state = await res.json();
                const nextState = !state.kill_switch;
                const r = await fetch('/api/cmd?token=' + encodeURIComponent(adminToken), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'kill_switch', enabled: nextState })
                });
                const d = await r.json();
                fetchData();
            } catch(e) {}
        }

        // Add/Update User Form
        document.getElementById('user-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const uid = document.getElementById('form-user-id').value.trim();
            const cap = parseFloat(document.getElementById('form-user-capital').value);
            const conf = parseFloat(document.getElementById('form-user-conf').value);
            const risk = parseFloat(document.getElementById('form-user-risk').value);
            const lev = parseInt(document.getElementById('form-user-lev').value);

            if (!uid || isNaN(cap)) return;

            try {
                const registerPayload = {
                    telegram_id: uid,
                    api_key: 'BINGX_MOCK_KEY_' + uid,
                    api_secret: 'BINGX_MOCK_SECRET_' + uid
                };
                
                // First call register to ensure DB record exists
                const regRes = await fetch('/api/users/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(registerPayload)
                });
                
                // Then call put update for the specific params
                const updatePayload = {
                    capital: cap,
                    min_confidence: conf,
                    max_risk_pct: risk,
                    leverage: lev,
                    auto_trade: true
                };
                
                const upRes = await fetch(\`/api/users/\${uid}?token=\` + encodeURIComponent(adminToken), {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(updatePayload)
                });

                if (upRes.ok) {
                    alert('Lưu người dùng ' + uid + ' thành công!');
                    document.getElementById('user-form').reset();
                    fetchData();
                } else {
                    alert('Lưu thất bại!');
                }
            } catch (err) {
                alert('Lỗi khi lưu user!');
            }
        });

        // Delete user helper
        async function deleteUser(uid) {
            if (!confirm('Bạn có chắc chắn muốn xóa user ' + uid + ' khỏi hệ thống?')) return;
            try {
                const res = await fetch(\`/api/users/\${uid}?token=\` + encodeURIComponent(adminToken), {
                    method: 'DELETE'
                });
                if (res.ok) {
                    fetchData();
                } else {
                    alert('Không thể xóa user!');
                }
            } catch (e) {}
        }

        // Close position helper
        async function closeUserPosition(uid, symbol) {
            if (!confirm('Bạn có muốn đóng vị thế ' + symbol + ' của user ' + uid + '?')) return;
            try {
                const res = await fetch('/api/user/close', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_id: uid, symbol: symbol })
                });
                const d = await res.json();
                alert(d.msg || 'Yêu cầu đóng lệnh đã gửi!');
                fetchData();
            } catch (e) {}
        }

        // Manual Signal Dispatcher Form
        document.getElementById('signal-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            const symbol = document.getElementById('sig-symbol').value.toUpperCase().trim();
            const direction = document.getElementById('sig-direction').value;
            const confidence = parseFloat(document.getElementById('sig-confidence').value);
            const entry = parseFloat(document.getElementById('sig-entry').value);
            const sl = parseFloat(document.getElementById('sig-sl').value);
            const tp1 = parseFloat(document.getElementById('sig-tp1').value);
            const tp2 = parseFloat(document.getElementById('sig-tp2').value);

            const signalPayload = {
                symbol: symbol,
                final: direction,
                confidence: confidence,
                plan: {
                    entry: entry,
                    sl: sl,
                    tp1: tp1,
                    tp2: tp2
                },
                timestamp: new Date().toISOString()
            };

            try {
                const res = await fetch('/api/cmd?token=' + encodeURIComponent(adminToken), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        action: 'trigger_signal',
                        signal: signalPayload
                    })
                });
                const d = await res.json();
                alert(d.msg || 'Đã gửi tín hiệu giao dịch!');
            } catch(err) {
                alert('Không thể gửi tín hiệu!');
            }
        });

        // Core data polling
        async function fetchData() {
            if (!adminToken) return;

            try {
                // Fetch state
                const resState = await fetch('/api/state?token=' + encodeURIComponent(adminToken));
                if (resState.status === 401) {
                    logout();
                    return;
                }
                const state = await resState.json();

                // Fetch users
                const resUsers = await fetch('/api/users?token=' + encodeURIComponent(adminToken));
                const users = await resUsers.json();

                // Render Header info
                const headerAuto = document.getElementById('header-auto-badge');
                headerAuto.innerText = state.auto_trade ? 'HOẠT ĐỘNG' : 'TẠM DỪNG';
                headerAuto.className = state.auto_trade ? 'text-emerald-400 font-bold animate-pulse' : 'text-amber-500 font-bold';

                const headerKill = document.getElementById('header-kill-badge');
                headerKill.innerText = state.kill_switch ? 'Armed (KHẨN CẤP)' : 'Standby (AN TOÀN)';
                headerKill.className = state.kill_switch ? 'text-red-500 font-black animate-bounce' : 'text-emerald-400 font-bold';

                // Render Users
                document.getElementById('user-count').innerText = users.length;
                const userBox = document.getElementById('users-container');
                userBox.innerHTML = '';
                
                users.forEach(u => {
                    const row = document.createElement('div');
                    row.className = 'flex justify-between items-center bg-[#070b16] border border-[#141d2e] rounded-lg p-3 text-xs';
                    row.innerHTML = \`
                        <div>
                            <span class="font-bold block text-emerald-400">UID: \${u.telegram_id}</span>
                            <span class="text-[10px] text-[#718096]">Capital: \$\${u.capital.toFixed(2)} | Lev: \${u.leverage}x | Risk: \${u.max_risk_pct}%</span>
                            <span class="text-[10px] text-[#718096] block">PnL Tích Lũy: <b class="\${u.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}">\${u.total_pnl >= 0 ? '+' : ''}\$\${u.total_pnl.toFixed(2)}</b></span>
                        </div>
                        <div class="flex gap-1.5">
                            <button onclick="editUserFill('\${u.telegram_id}', \${u.capital}, \${u.min_confidence}, \${u.max_risk_pct}, \${u.leverage})" 
                                    class="p-1.5 rounded bg-blue-950/40 text-blue-400 border border-blue-500/20 hover:bg-blue-900/40 font-bold text-[10px] uppercase">
                                Sửa
                            </button>
                            <button onclick="deleteUser('\${u.telegram_id}')" 
                                    class="p-1.5 rounded bg-red-950/40 text-red-400 border border-red-500/20 hover:bg-red-900/40 font-bold text-[10px] uppercase">
                                Xóa
                            </button>
                        </div>
                    \`;
                    userBox.appendChild(row);
                });

                // Render Open Positions
                const posBox = document.getElementById('positions-container');
                posBox.innerHTML = '';
                
                if (state.positions && state.positions.length > 0) {
                    state.positions.forEach(p => {
                        const card = document.createElement('div');
                        card.className = \`border rounded-lg p-3 text-xs bg-[#070b16] \${p.direction === 'LONG' ? 'border-emerald-500/20' : 'border-red-500/20'}\`;
                        card.innerHTML = \`
                            <div class="flex justify-between items-start">
                                <div>
                                    <span class="font-black \${p.direction === 'LONG' ? 'text-emerald-400' : 'text-red-400'}">\${p.direction} - \${p.symbol}</span>
                                    <span class="text-[10px] text-[#718096] block font-mono">Qty: \${p.qty} | User: \${p.user_id}</span>
                                </div>
                                <button onclick="closeUserPosition('\${p.user_id}', '\${p.symbol}')" 
                                        class="px-2 py-1 text-[10px] font-black bg-red-950/50 text-red-400 border border-red-500/20 rounded hover:bg-red-900/40">
                                    ĐÓNG VỊ THẾ
                                </button>
                            </div>
                        \`;
                        posBox.appendChild(card);
                    });
                } else {
                    posBox.innerHTML = '<div class="text-[#718096] text-center text-xs py-4 font-mono">Không có vị thế hoạt động.</div>';
                }

                // Render real-time orderbook & liquidations dynamically
                await updateMarketDepth();

            } catch (err) {
                console.error(err);
            }
        }

        function editUserFill(id, capital, min_confidence, max_risk_pct, leverage) {
            document.getElementById('form-user-id').value = id;
            document.getElementById('form-user-capital').value = capital;
            document.getElementById('form-user-conf').value = min_confidence;
            document.getElementById('form-user-risk').value = max_risk_pct;
            document.getElementById('form-user-lev').value = leverage;
        }

        // Pre-fill active asset price data to helper fields in Signal form
        function preFillSignal(symbol, entryPrice) {
            document.getElementById('sig-symbol').value = symbol;
            const selectEl = document.getElementById('sig-symbol-select');
            if (selectEl) {
                let found = false;
                for (let i = 0; i < selectEl.options.length; i++) {
                    if (selectEl.options[i].value === symbol) {
                        selectEl.selectedIndex = i;
                        found = true;
                        break;
                    }
                }
                if (!found) {
                    selectEl.value = ""; // Show as Khác/custom
                }
            }
            document.getElementById('sig-entry').value = entryPrice.toFixed(2);
            const atr = entryPrice * 0.012;
            const direction = document.getElementById('sig-direction').value;
            if (direction === 'LONG') {
                document.getElementById('sig-sl').value = (entryPrice - atr).toFixed(2);
                document.getElementById('sig-tp1').value = (entryPrice + atr * 2).toFixed(2);
                document.getElementById('sig-tp2').value = (entryPrice + atr * 3.5).toFixed(2);
            } else {
                document.getElementById('sig-sl').value = (entryPrice + atr).toFixed(2);
                document.getElementById('sig-tp1').value = (entryPrice - atr * 2).toFixed(2);
                document.getElementById('sig-tp2').value = (entryPrice - atr * 3.5).toFixed(2);
            }
        }

        // Real-time Orderbook and Liquidation Map from Real API
        let lastSimulatedPrice = null;
        async function updateMarketDepth() {
            const symbolInput = document.getElementById('sig-symbol');
            if (!symbolInput) return;
            const symbol = symbolInput.value.toUpperCase().trim() || 'BTCUSDT';
            
            try {
                const res = await fetch(\`/api/market-depth?symbol=\${encodeURIComponent(symbol)}\`);
                if (!res.ok) throw new Error("API error");
                const data = await res.json();
                
                if (data.success) {
                    renderRealOrderbook(data.symbol, data.price, data.orderbook);
                    renderRealLiquidations(data.symbol, data.price, data.liquidation);
                } else {
                    renderSimulatedDepth(symbol);
                }
            } catch (err) {
                console.error("updateMarketDepth error:", err);
                renderSimulatedDepth(symbol);
            }
        }

        function renderRealOrderbook(symbol, price, ob) {
            const bidContainer = document.getElementById('bids-container');
            const askContainer = document.getElementById('asks-container');
            if (!bidContainer || !askContainer) return;
            
            bidContainer.innerHTML = '';
            askContainer.innerHTML = '';

            let bids = ob.bids || [];
            let asks = ob.asks || [];
            
            if (bids.length === 0 || asks.length === 0) {
                const step = price * 0.0001;
                for (let i = 1; i <= 6; i++) {
                    const askP = price + i * step;
                    const askQ = Math.random() * 1.5 + 0.1 + (i === 3 ? 5 : 0);
                    asks.push([askP, askQ]);

                    const bidP = price - i * step;
                    const bidQ = Math.random() * 1.5 + 0.1 + (i === 4 ? 6 : 0);
                    bids.push([bidP, bidQ]);
                }
                asks.sort((a, b) => a[0] - b[0]);
                bids.sort((a, b) => b[0] - a[0]);
            }

            let maxUsd = 0.001;
            const formattedAsks = asks.slice(0, 6).map(a => {
                const p = parseFloat(a[0]);
                const q = parseFloat(a[1]);
                const usd = p * q;
                if (usd > maxUsd) maxUsd = usd;
                return { price: p, qty: q, usd: usd };
            });
            const formattedBids = bids.slice(0, 6).map(b => {
                const p = parseFloat(b[0]);
                const q = parseFloat(b[1]);
                const usd = p * q;
                if (usd > maxUsd) maxUsd = usd;
                return { price: p, qty: q, usd: usd };
            });

            [...formattedAsks].reverse().forEach(ask => {
                const row = document.createElement('div');
                row.className = 'flex justify-between relative py-0.5 px-1 hover:bg-white/5';
                const pct = (ask.usd / maxUsd) * 100;
                row.innerHTML = \`
                    <div class="absolute right-0 top-0 bottom-0 bg-red-500/5 transition-all" style="width: \${pct}%"></div>
                    <span class="text-red-400 relative z-10 font-bold">\$\${ask.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 4})}</span>
                    <span class="text-gray-400 relative z-10">\${ask.qty.toFixed(3)}</span>
                    <span class="text-gray-600 relative z-10">\$\${Math.round(ask.usd).toLocaleString()}</span>
                \`;
                askContainer.appendChild(row);
            });

            formattedBids.forEach(bid => {
                const row = document.createElement('div');
                row.className = 'flex justify-between relative py-0.5 px-1 hover:bg-white/5';
                const pct = (bid.usd / maxUsd) * 100;
                row.innerHTML = \`
                    <div class="absolute right-0 top-0 bottom-0 bg-emerald-500/5 transition-all" style="width: \${pct}%"></div>
                    <span class="text-emerald-400 relative z-10 font-bold">\$\${bid.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 4})}</span>
                    <span class="text-gray-400 relative z-10">\${bid.qty.toFixed(3)}</span>
                    <span class="text-gray-600 relative z-10">\$\${Math.round(bid.usd).toLocaleString()}</span>
                \`;
                bidContainer.appendChild(row);
            });

            const bestBidWall = formattedBids.length > 0 ? formattedBids.reduce((m, b) => b.usd > m.usd ? b : m, formattedBids[0]) : { price, usd: 0 };
            const bestAskWall = formattedAsks.length > 0 ? formattedAsks.reduce((m, a) => a.usd > m.usd ? a : m, formattedAsks[0]) : { price, usd: 0 };
            document.getElementById('buy-wall-val').innerText = \`\$\${bestBidWall.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 2})} (\$\${Math.round(bestBidWall.usd).toLocaleString()})\`;
            document.getElementById('sell-wall-val').innerText = \`\$\${bestAskWall.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 2})} (\$\${Math.round(bestAskWall.usd).toLocaleString()})\`;

            const imbVal = ob.imbalance !== undefined ? ob.imbalance : 0;
            const imbDisp = document.getElementById('orderbook-imb');
            imbDisp.innerText = 'Imbalance: ' + (imbVal >= 0 ? '+' : '') + imbVal + '%';
            imbDisp.className = 'font-mono text-xs font-black ' + (imbVal >= 0 ? 'text-emerald-400' : 'text-red-400');

            const sigEntryInput = document.getElementById('sig-entry');
            if (sigEntryInput && !sigEntryInput.dataset.listenerAttached) {
                sigEntryInput.dataset.listenerAttached = "true";
                sigEntryInput.addEventListener('focus', function() {
                    if (!sigEntryInput.value) {
                        preFillSignal(symbol, price);
                    }
                });
            }
        }

        function renderRealLiquidations(symbol, price, liq) {
            const longBox = document.getElementById('long-liqs');
            const shortBox = document.getElementById('short-liqs');
            if (!longBox || !shortBox) return;
            
            longBox.innerHTML = '';
            shortBox.innerHTML = '';

            const leverages = [5, 10, 20, 50, 100];
            let longLiqs = liq.long_liq_levels || [];
            let shortLiqs = liq.short_liq_levels || [];

            if (longLiqs.length === 0 || shortLiqs.length === 0) {
                longLiqs = leverages.map(lev => {
                    const lp = price * (1 - 0.9 / lev);
                    return { leverage: lev, price: lp, distance_pct: ((price - lp) / price) * 100 };
                });
                shortLiqs = leverages.map(lev => {
                    const sp = price * (1 + 0.9 / lev);
                    return { leverage: lev, price: sp, distance_pct: ((sp - price) / price) * 100 };
                });
            }

            longLiqs.forEach(l => {
                const divL = document.createElement('div');
                divL.className = 'flex justify-between text-gray-400';
                divL.innerHTML = \`<span>\${l.leverage}x Đòn bẩy</span><span class="text-emerald-400 font-bold">\$\${l.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 4})} (\${l.distance_pct.toFixed(1)}%)</span>\`;
                longBox.appendChild(divL);
            });

            shortLiqs.forEach(s => {
                const divS = document.createElement('div');
                divS.className = 'flex justify-between text-gray-400';
                divS.innerHTML = \`<span>\${s.leverage}x Đòn bẩy</span><span class="text-red-400 font-bold">\$\${s.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 4})} (\${s.distance_pct.toFixed(1)}%)</span>\`;
                shortBox.appendChild(divS);
            });

            const cascadeBadge = document.getElementById('cascade-risk-badge');
            const hasCascade = liq.cascade_risk !== undefined ? liq.cascade_risk : false;
            cascadeBadge.innerText = hasCascade ? 'HIGH RISK' : 'NORMAL';
            cascadeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-black ' + (hasCascade ? 'bg-red-500/20 text-red-400 animate-pulse' : 'bg-emerald-500/10 text-emerald-400');

            const dominantSide = liq.dominant_side || "NEUTRAL";
            const domVal = document.getElementById('dominant-side-val');
            if (dominantSide === "LONG") {
                domVal.innerText = '🔼 LONG (BULLS DOMINANT)';
                domVal.className = 'font-bold text-xs text-emerald-400';
            } else if (dominantSide === "SHORT") {
                domVal.innerText = '🔽 SHORT (BEARS DOMINANT)';
                domVal.className = 'font-bold text-xs text-red-400';
            } else {
                domVal.innerText = '↕️ NEUTRAL (BALANCED MARKET)';
                domVal.className = 'font-bold text-xs text-gray-400';
            }
            
            const spread = liq.spread_pct || 0.0012;
            document.getElementById('spread-pct-val').innerText = spread.toFixed(4) + '%';
            document.getElementById('spread-pct-val').className = 'text-gray-200 font-bold';
        }

        function renderSimulatedDepth(symbol) {
            let basePrice = 92850.5;
            if (symbol.includes("ETH")) basePrice = 3500.0;
            else if (symbol.includes("SOL")) basePrice = 145.0;
            else if (symbol.includes("BNB")) basePrice = 580.0;
            else if (symbol.includes("ADA")) basePrice = 0.38;
            else if (symbol.includes("XRP")) basePrice = 0.58;
            else if (symbol.includes("DOGE")) basePrice = 0.12;

            if (lastSimulatedPrice === null || Math.abs(lastSimulatedPrice - basePrice) / basePrice > 0.5) {
                lastSimulatedPrice = basePrice;
            }
            lastSimulatedPrice += (Math.random() - 0.5) * (basePrice * 0.001);

            renderRealOrderbook(symbol, lastSimulatedPrice, { ok: false });
            renderRealLiquidations(symbol, lastSimulatedPrice, { ok: false });
        }

        // Initialize Page
        checkAuth();
    </script>
</body>
</html>
"""


@app.get("/admin", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard_admin(request: Request, token: str = Query(default="")):
    return HTMLResponse(content=ADMIN_DASHBOARD_HTML, status_code=200)


MINIAPP_CONNECT_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
    <title>SignalBot API Registration</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Google Fonts: Inter & Space Grotesk -->
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght=400;500;600;700&family=Space+Grotesk:wght=500;700&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    fontFamily: {
                        sans: ['Inter', 'sans-serif'],
                        display: ['Space Grotesk', 'sans-serif'],
                        mono: ['JetBrains Mono', 'monospace'],
                    }
                }
            }
        }
    </script>
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #030712;
            color: #f3f4f6;
            -webkit-tap-highlight-color: transparent;
        }
    </style>
</head>
<body class="flex flex-col min-h-screen px-4 py-6 justify-center">
    <div class="max-w-md w-full mx-auto bg-gray-900/60 border border-gray-800/80 rounded-2xl p-6 shadow-2xl backdrop-blur-xl">
        <!-- Header -->
        <div class="text-center mb-8">
            <div class="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-emerald-500/10 text-emerald-400 mb-3 border border-emerald-500/20">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
            </div>
            <h1 class="text-2xl font-bold font-display tracking-tight text-white mb-1">SignalBot Auto-Trade</h1>
            <p class="text-sm text-gray-400">Kết nối API BingX để tự động copy trade real-time</p>
        </div>

        <!-- Form -->
        <form id="registerForm" onsubmit="handleRegister(event)" class="space-y-5">
            <div>
                <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Telegram UID</label>
                <input type="text" id="telegram_id" required 
                    class="w-full bg-gray-950 border border-gray-800 rounded-xl px-4 py-3 text-white font-mono placeholder-gray-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/50 transition-all"
                    placeholder="Nhập Telegram UID của bạn...">
            </div>

            <div>
                <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">BingX API Key</label>
                <input type="text" id="api_key" required 
                    class="w-full bg-gray-950 border border-gray-800 rounded-xl px-4 py-3 text-white font-mono placeholder-gray-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/50 transition-all"
                    placeholder="Dán API Key...">
            </div>

            <div>
                <label class="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">BingX API Secret</label>
                <input type="password" id="api_secret" required 
                    class="w-full bg-gray-950 border border-gray-800 rounded-xl px-4 py-3 text-white font-mono placeholder-gray-600 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/50 transition-all"
                    placeholder="Dán API Secret...">
            </div>

            <div class="pt-2">
                <button type="submit" id="submitBtn"
                    class="w-full bg-emerald-500 hover:bg-emerald-600 text-gray-950 font-bold py-3.5 rounded-xl shadow-lg shadow-emerald-500/20 active:scale-[0.98] transition-all flex items-center justify-center space-x-2">
                    <span>Kết Nối Tài Khoản</span>
                </button>
            </div>
        </form>

        <!-- Result Box -->
        <div id="resultBox" class="hidden mt-6 p-4 rounded-xl border"></div>
    </div>

    <!-- Script -->
    <script>
        // Auto fill UID from query params
        window.addEventListener('DOMContentLoaded', () => {
            const params = new URLSearchParams(window.location.search);
            const uid = params.get('uid') || params.get('telegram_id') || params.get('id');
            if (uid) {
                document.getElementById('telegram_id').value = uid;
            }
        });

        async function handleRegister(e) {
            e.preventDefault();
            const submitBtn = document.getElementById('submitBtn');
            const resultBox = document.getElementById('resultBox');
            
            const telegram_id = document.getElementById('telegram_id').value.trim();
            const api_key = document.getElementById('api_key').value.trim();
            const api_secret = document.getElementById('api_secret').value.trim();
            
            submitBtn.disabled = true;
            submitBtn.innerHTML = \`
                <svg class="animate-spin h-5 w-5 mr-3 text-gray-950" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" fill="none"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span>Đang kết nối...</span>
            \`;
            
            resultBox.classList.add('hidden');
            
            try {
                const response = await fetch('/api/users/register', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ telegram_id, api_key, api_secret })
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    resultBox.className = "mt-6 p-4 rounded-xl bg-emerald-500/10 border-emerald-500/20 text-emerald-400 space-y-2";
                    resultBox.innerHTML = \`
                        <div class="font-bold flex items-center space-x-2">
                            <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <span>Kết Nối Thành Công!</span>
                        </div>
                        <p class="text-xs text-gray-300">Tài khoản của bạn đã được liên kết với hệ thống auto-trade.</p>
                        <div class="pt-2 text-xs border-t border-emerald-500/10 space-y-1">
                            <div>• Tier: <span class="font-bold text-white">\${data.label || data.tier}</span></div>
                            <div>• Số dư: <span class="font-bold text-white">\${data.capital}</span></div>
                            <div>• Đòn bẩy tối đa: <span class="font-bold text-white">\${data.leverage || 'Tự động'}x</span></div>
                        </div>
                    \`;
                } else {
                    throw new Error(data.detail || 'Không thể liên kết API Key. Vui lòng kiểm tra lại.');
                }
            } catch (err) {
                resultBox.className = "mt-6 p-4 rounded-xl bg-red-500/10 border-red-500/20 text-red-400 space-y-1 text-sm";
                resultBox.innerHTML = \`
                    <div class="font-bold flex items-center space-x-2">
                        <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                        </svg>
                        <span>Lỗi Kết Nối</span>
                    </div>
                    <p class="text-xs text-gray-300">\${err.message}</p>
                \`;
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<span>Kết Nối Tài Khoản</span>';
                resultBox.classList.remove('hidden');
            }
        }
    </script>
</body>
</html>
"""

MINIAPP_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
    <title>SignalBot User Dashboard</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght=400;500;600;700&family=Space+Grotesk:wght=500;700&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    fontFamily: {
                        sans: ['Inter', 'sans-serif'],
                        display: ['Space Grotesk', 'sans-serif'],
                        mono: ['JetBrains Mono', 'monospace'],
                    }
                }
            }
        }
    </script>
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #030712;
            color: #f3f4f6;
            -webkit-tap-highlight-color: transparent;
        }
        .shimmer {
            background: linear-gradient(90deg, #1f2937 25%, #374151 50%, #1f2937 75%);
            background-size: 200% 100%;
            animation: loading 1.5s infinite;
        }
        @keyframes loading {
            0% { background-position: 200% 0; }
            100% { background-position: -200% 0; }
        }
    </style>
</head>
<body class="min-h-screen px-4 py-6">
    <div class="max-w-md mx-auto space-y-6">
        <!-- Header -->
        <div class="flex items-center justify-between">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-xl bg-emerald-500/10 flex items-center justify-center border border-emerald-500/20 text-emerald-400">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 8v8m-4-5v5m-4-2v2m-2 4h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                    </svg>
                </div>
                <div>
                    <h1 class="text-lg font-bold font-display text-white">My Dashboard</h1>
                    <p class="text-xs text-gray-400" id="uid-display">Telegram: ...</p>
                </div>
            </div>
            <button onclick="loadData()" class="p-2 rounded-xl bg-gray-900 border border-gray-800 text-gray-400 hover:text-white transition-all">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 4.89M9 11l3-3 3 3m0 0a1 1 0 01-1 1H10a1 1 0 01-1-1z" />
                </svg>
            </button>
        </div>

        <!-- ID Input for testing if opened in standard browser without query param -->
        <div id="uid-input-box" class="hidden p-4 bg-gray-900/40 border border-gray-800 rounded-xl space-y-3">
            <p class="text-xs text-gray-400">Vui lòng nhập Telegram UID để xem dữ liệu:</p>
            <div class="flex space-x-2">
                <input type="text" id="manual-uid" class="flex-1 bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-sm text-white font-mono" placeholder="Nhập Telegram UID...">
                <button onclick="setManualUid()" class="bg-emerald-500 hover:bg-emerald-600 text-gray-950 font-bold px-4 py-2 rounded-lg text-sm">Xem</button>
            </div>
        </div>

        <!-- Profile Overview Card -->
        <div class="bg-gray-900/60 border border-gray-800 rounded-2xl p-5 space-y-4 shadow-xl backdrop-blur-md">
            <div class="grid grid-cols-2 gap-4">
                <div class="bg-gray-950/60 p-3.5 rounded-xl border border-gray-800/60">
                    <span class="text-xs text-gray-400 block mb-1">Số dư (Equity)</span>
                    <span class="text-lg font-bold text-white font-mono" id="val-balance">\$-.--</span>
                </div>
                <div class="bg-gray-950/60 p-3.5 rounded-xl border border-gray-800/60">
                    <span class="text-xs text-gray-400 block mb-1">Cấp độ (Tier)</span>
                    <span class="text-sm font-bold text-emerald-400 uppercase tracking-wider block mt-1" id="val-tier">-</span>
                </div>
            </div>

            <div class="flex items-center justify-between pt-2 border-t border-gray-800/60">
                <div class="flex items-center space-x-2">
                    <span class="w-2.5 h-2.5 rounded-full" id="status-dot"></span>
                    <span class="text-xs text-gray-300" id="val-status">Trạng thái: Đang tải...</span>
                </div>
                <span class="text-xs font-mono text-gray-500" id="val-confidence">Conf tối thiểu: --%</span>
            </div>
        </div>

        <!-- Section: Active Positions -->
        <div>
            <div class="flex items-center justify-between mb-3.5">
                <h2 class="text-sm font-bold uppercase tracking-wider text-gray-400 font-display">Vị thế đang mở</h2>
                <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-gray-900 border border-gray-800 text-gray-400" id="pos-count">0</span>
            </div>

            <!-- Position List -->
            <div id="position-list" class="space-y-3">
                <!-- Loading Shimmer -->
                <div class="shimmer h-24 rounded-xl opacity-25"></div>
            </div>
        </div>

        <!-- Section: Latest Market Signals -->
        <div>
            <div class="flex items-center justify-between mb-3.5">
                <h2 class="text-sm font-bold uppercase tracking-wider text-gray-400 font-display">Tín hiệu bot mới nhất</h2>
            </div>

            <div id="signal-list" class="space-y-3">
                <div class="shimmer h-20 rounded-xl opacity-10"></div>
            </div>
        </div>
    </div>

    <!-- Script -->
    <script>
        let currentUid = '';

        window.addEventListener('DOMContentLoaded', () => {
            const params = new URLSearchParams(window.location.search);
            currentUid = params.get('uid') || params.get('telegram_id') || params.get('id');
            
            if (!currentUid) {
                document.getElementById('uid-input-box').classList.remove('hidden');
            } else {
                document.getElementById('uid-display').innerText = \`Telegram UID: \${currentUid}\`;
                loadData();
            }
        });

        function setManualUid() {
            const val = document.getElementById('manual-uid').value.trim();
            if (val) {
                currentUid = val;
                document.getElementById('uid-display').innerText = \`Telegram UID: \${currentUid}\`;
                document.getElementById('uid-input-box').classList.add('hidden');
                loadData();
            }
        }

        async function loadData() {
            if (!currentUid) return;
            
            try {
                const response = await fetch(\`/api/state?uid=\${currentUid}\`);
                const data = await response.json();
                
                if (data.error) {
                    throw new Error(data.error);
                }

                // Render Summary
                document.getElementById('val-balance').innerText = \`\$\${(data.stats.equity || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}\`;
                document.getElementById('val-tier').innerText = data.tier_label || data.tier;
                document.getElementById('val-confidence').innerText = \`Conf tối thiểu: \${data.min_confidence || 68}%\`;
                
                const statusDot = document.getElementById('status-dot');
                const statusText = document.getElementById('val-status');
                
                if (data.auto_trade && !data.kill_switch) {
                    statusDot.className = "w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse";
                    statusText.innerText = "Trạng thái: Hoạt động (Auto)";
                } else if (data.kill_switch) {
                    statusDot.className = "w-2.5 h-2.5 rounded-full bg-red-500";
                    statusText.innerText = "Trạng thái: Kill Switch ĐANG BẬT";
                } else {
                    statusDot.className = "w-2.5 h-2.5 rounded-full bg-amber-500";
                    statusText.innerText = "Trạng thái: Đang Tắt (Hủy Kích Hoạt)";
                }

                // Render Positions
                const posList = document.getElementById('position-list');
                const posCount = document.getElementById('pos-count');
                const positions = data.positions || [];
                
                posCount.innerText = positions.length;
                
                if (positions.length === 0) {
                    posList.innerHTML = \`
                        <div class="text-center py-8 bg-gray-900/30 border border-gray-800/40 rounded-xl">
                            <span class="text-gray-600 block text-2xl mb-1">📦</span>
                            <span class="text-xs text-gray-500">Chưa có vị thế nào được mở.</span>
                        </div>
                    \`;
                } else {
                    posList.innerHTML = '';
                    positions.forEach(p => {
                        const sideBg = p.direction === 'LONG' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-red-500/10 text-red-400 border-red-500/20';
                        const pnlColor = p.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400';
                        const pnlSign = p.pnl_pct >= 0 ? '+' : '';
                        
                        const div = document.createElement('div');
                        div.className = "bg-gray-900 border border-gray-800 rounded-xl p-4 shadow-md space-y-3";
                        div.innerHTML = \`
                            <div class="flex items-center justify-between">
                                <div class="flex items-center space-x-2">
                                    <span class="text-sm font-bold font-display text-white">\${p.symbol}</span>
                                    <span class="px-2 py-0.5 rounded text-[10px] font-bold border \${sideBg}">\${p.direction}</span>
                                </div>
                                <button onclick="closePosition('\${p.symbol}')" class="px-2.5 py-1 text-[10px] font-bold bg-red-500/10 text-red-400 hover:bg-red-500/20 rounded-lg border border-red-500/20 transition-all">Đóng</button>
                            </div>
                            
                            <div class="grid grid-cols-3 gap-2 pt-2 border-t border-gray-800/60 text-center">
                                <div>
                                    <span class="text-[10px] text-gray-500 block">Vào lệnh</span>
                                    <span class="text-xs font-semibold text-gray-300 font-mono">\$\${p.entry.toFixed(4)}</span>
                                </div>
                                <div>
                                    <span class="text-[10px] text-gray-500 block">Hiện tại</span>
                                    <span class="text-xs font-semibold text-gray-300 font-mono">\$\${p.current_price.toFixed(4)}</span>
                                </div>
                                <div>
                                    <span class="text-[10px] text-gray-500 block">Lợi nhuận (PnL)</span>
                                    <span class="text-xs font-bold \${pnlColor} font-mono">\${pnlSign}\${p.pnl_pct}%</span>
                                </div>
                            </div>
                        \`;
                        posList.appendChild(div);
                    });
                }

                // Render Signals
                const sigList = document.getElementById('signal-list');
                const signals = data.signals || [];
                
                if (signals.length === 0) {
                    sigList.innerHTML = \`
                        <div class="text-center py-6 bg-gray-900/30 border border-gray-800/40 rounded-xl">
                            <span class="text-xs text-gray-600">Không có tín hiệu gần đây.</span>
                        </div>
                    \`;
                } else {
                    sigList.innerHTML = '';
                    signals.slice(0, 3).forEach(s => {
                        const sideBg = s.final === 'LONG' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400';
                        
                        const div = document.createElement('div');
                        div.className = "bg-gray-900/40 border border-gray-800 rounded-xl p-3 flex items-center justify-between text-xs";
                        div.innerHTML = \`
                            <div class="space-y-1">
                                <div class="flex items-center space-x-2">
                                    <span class="font-bold text-white">\${s.symbol}</span>
                                    <span class="px-1.5 py-0.5 rounded text-[9px] font-bold \${sideBg}">\${s.final}</span>
                                </div>
                                <div class="text-[10px] text-gray-500">Confidence: \${s.confidence}% | Entry: \${s.plan?.entry || '-'}</div>
                            </div>
                            <span class="text-[10px] font-mono text-gray-600">\${s.timestamp || 'Mới'}</span>
                        \`;
                        sigList.appendChild(div);
                    });
                }

            } catch (err) {
                console.error("Dashboard error:", err);
            }
        }

        async function closePosition(symbol) {
            if (!confirm(\`Bạn có chắc muốn đóng ngay vị thế \${symbol} bằng lệnh MARKET?\`)) {
                return;
            }
            
            try {
                const response = await fetch('/api/user/close', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ user_id: currentUid, symbol })
                });
                
                const data = await response.json();
                alert(data.msg || 'Yêu cầu đóng vị thế đã được gửi.');
                setTimeout(loadData, 1500);
            } catch (err) {
                alert('Có lỗi xảy ra: ' + err.message);
            }
        }
    </script>
</body>
</html>
"""

@app.get("/miniapp/connect", response_class=HTMLResponse)
def get_miniapp_connect(request: Request):
    return HTMLResponse(content=MINIAPP_CONNECT_HTML, status_code=200)


@app.get("/my-dashboard", response_class=HTMLResponse)
@app.get("/miniapp/dashboard", response_class=HTMLResponse)
def get_miniapp_dashboard(request: Request):
    return HTMLResponse(content=MINIAPP_DASHBOARD_HTML, status_code=200)

`,
};
