//+------------------------------------------------------------------+
//| H4ThreeCandlePullback.mq5                                        |
//|                                                                    |
//| Live MT5 implementation of the H4 Three-Candle Pullback strategy  |
//| backtested in this repo's Python engine (src/xauusd_bot/).        |
//| Rules (identical to the Python backtest):                         |
//|   1. Direction: Daily 50 EMA on close. Yesterday's completed      |
//|      daily candle above the EMA -> BUY mode; below -> SELL mode.  |
//|   2. Setup: 3 consecutive closed H4 candles against the mode      |
//|      (red in BUY mode, green in SELL mode).                       |
//|   3. Entry: market order as soon as the next H4 bar opens.        |
//|   4. Stop = AtrMultiplier x ATR(14) on H4 (as of the candle that  |
//|      just completed the pattern). Take profit = same distance,   |
//|      RewardRiskRatio x on the opposite side.                      |
//|   5. Close at market if neither level is hit within               |
//|      TimeLimitHours.                                              |
//|   6. Only one position open at a time (by this EA's magic number).|
//|                                                                    |
//| SAFETY: AutoTrade defaults to false. With it false, the EA prints |
//| and alerts on every signal (direction, entry, SL, TP, lot size)   |
//| but sends no real order -- use this to watch it on a demo/live    |
//| chart before ever setting AutoTrade = true.                       |
//|                                                                    |
//| NOT COMPILE-TESTED IN A REAL METAEDITOR -- written carefully from |
//| the MQL5 trade API, but compile it yourself first and report any  |
//| errors back before trusting it on an account.                     |
//+------------------------------------------------------------------+
#property copyright "Backtested in xauusd-h4-pullback-bot"
#property version   "1.00"

input group "=== Direction filter ==="
input int    InpEmaPeriod        = 50;     // Daily EMA period

input group "=== Setup / entry ==="
input int    InpPullbackCandles  = 3;       // consecutive against-direction H4 candles required
input int    InpAtrPeriod        = 14;      // H4 ATR period
input double InpAtrMultiplier    = 2.0;     // stop distance = multiplier x ATR(14)
input double InpRewardRiskRatio  = 1.0;     // take-profit distance, as a multiple of the stop distance

input group "=== Trade management ==="
input double InpTimeLimitHours   = 200.0;   // close at market if neither level hit by then
input ulong  InpMagicNumber      = 20260101;

input group "=== Risk ==="
input double InpRiskPercent      = 1.0;     // % of account equity risked per trade

input group "=== Safety ==="
input bool   InpAutoTrade        = false;   // false = alert only, no real orders. true = trades live.
input int    InpSlippagePoints   = 20;      // max acceptable slippage, in points

int emaHandle;
int atrHandle;
datetime lastSeenH4BarTime = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   emaHandle = iMA(_Symbol, PERIOD_D1, InpEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   atrHandle = iATR(_Symbol, PERIOD_H4, InpAtrPeriod);
   if(emaHandle == INVALID_HANDLE || atrHandle == INVALID_HANDLE)
   {
      Print("H4ThreeCandlePullback: failed to create indicator handles");
      return(INIT_FAILED);
   }
   PrintFormat("H4ThreeCandlePullback initialised on %s. AutoTrade=%s",
               _Symbol, (InpAutoTrade ? "TRUE (live orders)" : "false (alerts only)"));
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(emaHandle);
   IndicatorRelease(atrHandle);
}

//+------------------------------------------------------------------+
void OnTick()
{
   ManageOpenPosition();

   // only evaluate the setup once, right as a NEW H4 bar begins -- this
   // is "enter at the open of the next H4 candle" in live terms.
   datetime currentH4BarTime = iTime(_Symbol, PERIOD_H4, 0);
   if(currentH4BarTime == lastSeenH4BarTime)
      return;
   bool isFirstCallSinceStart = (lastSeenH4BarTime == 0);
   lastSeenH4BarTime = currentH4BarTime;
   if(isFirstCallSinceStart)
      return; // don't act on an incomplete picture right at EA startup

   EvaluateSetup();
}

//+------------------------------------------------------------------+
// +1 = BUY mode, -1 = SELL mode, 0 = not enough data yet
int GetDailyMode()
{
   double emaBuf[];
   ArraySetAsSeries(emaBuf, true);
   if(CopyBuffer(emaHandle, 0, 1, 1, emaBuf) <= 0)
      return 0; // EMA as of yesterday's completed daily candle

   double yesterdayClose = iClose(_Symbol, PERIOD_D1, 1);
   if(yesterdayClose <= 0)
      return 0;

   return (yesterdayClose > emaBuf[0]) ? 1 : -1;
}

//+------------------------------------------------------------------+
bool PullbackPatternPresent(int mode)
{
   for(int i = 1; i <= InpPullbackCandles; i++)
   {
      double o = iOpen(_Symbol, PERIOD_H4, i);
      double c = iClose(_Symbol, PERIOD_H4, i);
      if(mode == 1)
      {
         if(!(c < o)) return false; // need red
      }
      else
      {
         if(!(c > o)) return false; // need green
      }
   }
   return true;
}

//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == (long)InpMagicNumber)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
void EvaluateSetup()
{
   if(HasOpenPosition())
      return; // one trade at a time

   int mode = GetDailyMode();
   if(mode == 0)
      return;

   if(!PullbackPatternPresent(mode))
      return;

   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(atrHandle, 0, 1, 1, atrBuf) <= 0)
      return; // ATR as of the H4 candle that just completed the pattern
   double atr = atrBuf[0];
   if(atr <= 0)
      return;

   double stopDistance = InpAtrMultiplier * atr;
   double tpDistance   = InpRewardRiskRatio * stopDistance;

   if(mode == 1)
      OpenTrade(ORDER_TYPE_BUY, stopDistance, tpDistance);
   else
      OpenTrade(ORDER_TYPE_SELL, stopDistance, tpDistance);
}

//+------------------------------------------------------------------+
double CalculateLotSize(double stopDistance)
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskMoney = equity * (InpRiskPercent / 100.0);

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize <= 0 || tickValue <= 0)
      return 0.0;

   double lossPerLot = (stopDistance / tickSize) * tickValue;
   if(lossPerLot <= 0)
      return 0.0;

   double rawLots = riskMoney / lossPerLot;

   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(lotStep <= 0)
      return 0.0;

   // round DOWN to the lot step -- never silently risk more than InpRiskPercent
   double steps = MathFloor(rawLots / lotStep + 1e-9);
   double lots = steps * lotStep;
   lots = MathMin(lots, maxLot);
   if(lots < minLot)
      return 0.0;

   return NormalizeDouble(lots, 2);
}

//+------------------------------------------------------------------+
void OpenTrade(ENUM_ORDER_TYPE type, double stopDistance, double tpDistance)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      Print("H4ThreeCandlePullback: could not read current tick, skipping signal");
      return;
   }
   double entryPrice = (type == ORDER_TYPE_BUY) ? tick.ask : tick.bid;

   double sl, tp;
   if(type == ORDER_TYPE_BUY)
   {
      sl = entryPrice - stopDistance;
      tp = entryPrice + tpDistance;
   }
   else
   {
      sl = entryPrice + stopDistance;
      tp = entryPrice - tpDistance;
   }
   sl = NormalizeDouble(sl, _Digits);
   tp = NormalizeDouble(tp, _Digits);

   double lots = CalculateLotSize(stopDistance);

   string msg = StringFormat(
      "%s SIGNAL: %s %.2f lots @ %.5f  SL=%.5f  TP=%.5f  (stop distance=%.5f)",
      _Symbol, (type == ORDER_TYPE_BUY ? "BUY" : "SELL"), lots, entryPrice, sl, tp, stopDistance);
   Print(msg);
   Alert(msg);
   SendNotification(msg); // push to MT5 mobile, if enabled in Terminal > Options > Notifications

   if(lots < SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
   {
      Print("H4ThreeCandlePullback: position size rounds below the broker's minimum lot at this risk % -- no order sent.");
      return;
   }

   if(!InpAutoTrade)
   {
      Print("H4ThreeCandlePullback: InpAutoTrade is false -- alert only, no order sent.");
      return;
   }

   MqlTradeRequest request;
   MqlTradeResult  result;
   ZeroMemory(request);
   ZeroMemory(result);
   request.action       = TRADE_ACTION_DEAL;
   request.symbol        = _Symbol;
   request.volume        = lots;
   request.type          = type;
   request.price         = entryPrice;
   request.sl            = sl;
   request.tp            = tp;
   request.deviation     = InpSlippagePoints;
   request.magic         = InpMagicNumber;
   request.type_filling  = ORDER_FILLING_FOK;
   request.comment       = "H4ThreeCandlePullback";

   if(!OrderSend(request, result))
      PrintFormat("H4ThreeCandlePullback: OrderSend failed, retcode=%d comment=%s", result.retcode, result.comment);
   else
      Print("H4ThreeCandlePullback: order placed, ticket=", result.order);
}

//+------------------------------------------------------------------+
void ManageOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)InpMagicNumber) continue;

      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      double hoursOpen = (double)(TimeCurrent() - openTime) / 3600.0;
      if(hoursOpen >= InpTimeLimitHours)
         CloseByTimeLimit(ticket);
   }
}

//+------------------------------------------------------------------+
void CloseByTimeLimit(ulong ticket)
{
   if(!PositionSelectByTicket(ticket))
      return;

   string symbol = PositionGetString(POSITION_SYMBOL);
   double volume = PositionGetDouble(POSITION_VOLUME);
   ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
      return;

   MqlTradeRequest request;
   MqlTradeResult  result;
   ZeroMemory(request);
   ZeroMemory(result);
   request.action      = TRADE_ACTION_DEAL;
   request.position    = ticket;
   request.symbol       = symbol;
   request.volume       = volume;
   request.type         = (posType == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
   request.price        = (request.type == ORDER_TYPE_SELL) ? tick.bid : tick.ask;
   request.deviation    = InpSlippagePoints;
   request.magic        = InpMagicNumber;
   request.type_filling = ORDER_FILLING_FOK;
   request.comment      = "H4-3CP time limit"; // many brokers truncate/reject order comments over ~31 chars

   if(!InpAutoTrade)
   {
      Print("H4ThreeCandlePullback: time limit reached on ticket ", ticket, " but AutoTrade is false -- not closing automatically.");
      Alert(StringFormat("A position has reached the %.0f-hour time limit -- close it manually (AutoTrade is off).", InpTimeLimitHours));
      return;
   }

   if(!OrderSend(request, result))
      Print("H4ThreeCandlePullback: time-limit close failed for ticket ", ticket, ", retcode=", result.retcode);
   else
      Print("H4ThreeCandlePullback: ticket ", ticket, " closed by time limit");
}
