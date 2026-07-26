def adx(highs, lows, closes, period=14):
    import math
    if len(closes) < period * 2:
        return 0, 0, 0
    
    def rma(series, length):
        alpha = 1.0 / length
        res = [series[0]]
        for val in series[1:]:
            res.append(alpha * val + (1 - alpha) * res[-1])
        return res

    tr = [0]
    plus_dm = [0]
    minus_dm = [0]
    
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        pc = closes[i-1]
        ph = highs[i-1]
        pl = lows[i-1]
        
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        
        up_move = h - ph
        down_move = pl - l
        
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)
            
        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)
            
    # Wilder used a specific smoothing which is essentially RMA
    # First value is sum of first period
    tr_sum = sum(tr[1:period+1])
    pdm_sum = sum(plus_dm[1:period+1])
    mdm_sum = sum(minus_dm[1:period+1])
    
    smooth_tr = [tr_sum]
    smooth_pdm = [pdm_sum]
    smooth_mdm = [mdm_sum]
    
    for i in range(period+1, len(closes)):
        smooth_tr.append(smooth_tr[-1] - (smooth_tr[-1]/period) + tr[i])
        smooth_pdm.append(smooth_pdm[-1] - (smooth_pdm[-1]/period) + plus_dm[i])
        smooth_mdm.append(smooth_mdm[-1] - (smooth_mdm[-1]/period) + minus_dm[i])
        
    plus_di = []
    minus_di = []
    dx = []
    
    for i in range(len(smooth_tr)):
        str_val = smooth_tr[i]
        spdm_val = smooth_pdm[i]
        smdm_val = smooth_mdm[i]
        
        if str_val == 0:
            pdi = 0
            mdi = 0
        else:
            pdi = 100 * spdm_val / str_val
            mdi = 100 * smdm_val / str_val
            
        plus_di.append(pdi)
        minus_di.append(mdi)
        
        if pdi + mdi == 0:
            dx.append(0)
        else:
            dx.append(100 * abs(pdi - mdi) / (pdi + mdi))
            
    # Calculate ADX (SMA of DX first, then RMA)
    if len(dx) < period:
        return 0, plus_di[-1], minus_di[-1]
        
    adx_val = sum(dx[:period]) / period
    for i in range(period, len(dx)):
        adx_val = ((adx_val * (period - 1)) + dx[i]) / period
        
    return adx_val, plus_di[-1], minus_di[-1]

# Test
print(adx([10,12,15,14,16]*10, [8,10,12,11,14]*10, [9,11,14,12,15]*10))
