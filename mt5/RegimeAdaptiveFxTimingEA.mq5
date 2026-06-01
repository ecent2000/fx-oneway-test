#property copyright "FX Oneway Factor Test"
#property link      "https://localhost"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

enum ENUM_SIGNAL_PRICE_MODE
{
   PRICE_BID_BAR_CLOSE = 0,
   PRICE_MID_FROM_TICK = 1
};

enum ENUM_MARKET_REGIME
{
   REGIME_UNKNOWN = 0,
   REGIME_NARROW_BULL = 1,
   REGIME_WIDE_BULL = 2,
   REGIME_WIDE_RANGE = 3,
   REGIME_WIDE_BEAR = 4,
   REGIME_NARROW_BEAR = 5
};

input string                 InpSymbol = "";
input ENUM_TIMEFRAMES        InpTimeframe = PERIOD_M15;
input int                    InpChannelLookback = 96;
input int                    InpAtrLookback = 48;
input int                    InpTrendLookback = 96;
input int                    InpMomentumLookback = 24;
input int                    InpBreakoutLookback = 48;
input int                    InpShortMaLookback = 16;
input int                    InpConfirmBars = 4;
input int                    InpLongConfirmBars = 4;
input int                    InpShortConfirmBars = 4;
input double                 InpBullThreshold = 0.03;
input double                 InpBearThreshold = 0.03;
input double                 InpFlatThreshold = 0.01;
input double                 InpNarrowWidthThreshold = 8.0;
input double                 InpWideRangeThreshold = 10.0;
input double                 InpMomentumEntry = 0.0003;
input double                 InpPullbackBuyZone = 0.33;
input double                 InpPullbackSellZone = 0.67;
input double                 InpLowerThird = 0.33;
input double                 InpUpperThird = 0.67;
input double                 InpStopAtrMult = 2.0;
input double                 InpLongStopAtrMult = 1.5;
input double                 InpShortStopAtrMult = 1.5;
input int                    InpMaxPositionBars = 96;
input int                    InpCooldownBars = 4;
input int                    InpMinRegimeBars = 2;
input double                 InpMinTargetAtrMult = 0.8;
input double                 InpLongMinTargetAtrMult = 0.8;
input double                 InpShortMinTargetAtrMult = 0.8;
input double                 InpMinTargetSpreadMult = 0.0;
input int                    InpAssumedSpreadPoints = 10;
input bool                   InpStrictLongFilter = false;
input double                 InpStrictLongTrendMult = 1.5;
input bool                   InpDisableWideRangeLongs = false;
input string                 InpDisabledEntryRegimes = "";
input string                 InpEntryHoursUtc = "";
input string                 InpLongEnabledRegimes = "wide_bull";
input string                 InpShortEnabledRegimes = "wide_bear";
input string                 InpLongEntryHoursUtc = "6-8";
input string                 InpShortEntryHoursUtc = "13-15";
input int                    InpEntryHourShiftHours = 0;
input ENUM_SIGNAL_PRICE_MODE InpPriceMode = PRICE_BID_BAR_CLOSE;

input bool                   InpAllowTrading = false;
input double                 InpLots = 0.01;
input ulong                  InpMagicNumber = 26060101;
input int                    InpMaxSpreadPoints = 30;
input int                    InpDeviationPoints = 20;
input int                    InpStopLossPoints = 0;
input int                    InpTakeProfitPoints = 0;
input bool                   InpCloseOnDeinit = false;

input string                 InpSignalLogFile = "regime_adaptive_signal_log.csv";
input string                 InpOrderLogFile = "regime_adaptive_order_log.csv";
input string                 InpErrorLogFile = "regime_adaptive_error_log.csv";
input bool                   InpAppendLog = true;
input bool                   InpUseCommonLogFolder = true;
input bool                   InpUniqueLogFiles = true;

struct PositionSnapshot
{
   int      own_count;
   int      unknown_count;
   int      direction;
   ulong    ticket;
   double   volume;
   datetime open_time;
   long     type;
};

struct FactorSnapshot
{
   double close;
   double rolling_high;
   double rolling_low;
   double channel_width;
   double atr;
   double width_score;
   double range_pos;
   double trend_slope;
   double momentum;
   double short_ma;
   double prev_high;
   double prev_low;
};

CTrade  g_trade;
string  g_symbol = "";
string  g_run_id = "";
string  g_signal_log_file = "";
string  g_order_log_file = "";
string  g_error_log_file = "";
datetime g_last_closed_bar_time = 0;
int     g_virtual_position = 0;
int     g_virtual_position_bars = 0;
int     g_cooldown_bars_remaining = 0;
double  g_virtual_entry_price = 0.0;
double  g_virtual_entry_atr = 0.0;
ENUM_MARKET_REGIME g_confirmed_regime = REGIME_UNKNOWN;
ENUM_MARKET_REGIME g_pending_regime = REGIME_UNKNOWN;
int     g_pending_regime_count = 0;
int     g_confirmed_regime_age = 0;
int     g_confirmed_regime_observed_count = 0;
double  g_live_entry_price = 0.0;
double  g_live_entry_atr = 0.0;

string TimeframeToString(const ENUM_TIMEFRAMES timeframe)
{
   switch(timeframe)
   {
      case PERIOD_M1:  return "M1";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";
      default:         return EnumToString(timeframe);
   }
}

string PriceModeToString(const ENUM_SIGNAL_PRICE_MODE mode)
{
   if(mode == PRICE_MID_FROM_TICK)
      return "PRICE_MID_FROM_TICK";
   return "PRICE_BID_BAR_CLOSE";
}

string DirectionToString(const int direction)
{
   if(direction > 0)
      return "LONG";
   if(direction < 0)
      return "SHORT";
   return "FLAT";
}

string RegimeToString(const ENUM_MARKET_REGIME regime)
{
   switch(regime)
   {
      case REGIME_NARROW_BULL: return "narrow_bull";
      case REGIME_WIDE_BULL:   return "wide_bull";
      case REGIME_WIDE_RANGE:  return "wide_range";
      case REGIME_WIDE_BEAR:   return "wide_bear";
      case REGIME_NARROW_BEAR: return "narrow_bear";
      default:                 return "unknown";
   }
}

bool StringContainsToken(string csv, const string token)
{
   StringToLower(csv);
   string parts[];
   const int count = StringSplit(csv, StringGetCharacter(",", 0), parts);
   for(int i = 0; i < count; i++)
   {
      string item = parts[i];
      StringTrimLeft(item);
      StringTrimRight(item);
      if(item == token)
         return true;
   }
   return false;
}

bool EntryRegimeOk()
{
   string text = InpDisabledEntryRegimes;
   StringToLower(text);
   StringTrimLeft(text);
   StringTrimRight(text);
   if(text == "" || text == "none" || text == "all_enabled")
      return true;

   return !StringContainsToken(text, RegimeToString(g_confirmed_regime));
}

bool EnabledRegimeOk(string text)
{
   StringToLower(text);
   StringTrimLeft(text);
   StringTrimRight(text);
   if(text == "" || text == "all" || text == "any" || text == "all_enabled")
      return true;
   if(text == "none")
      return false;

   return StringContainsToken(text, RegimeToString(g_confirmed_regime));
}

bool EntryRegimeOk(const int direction)
{
   if(direction > 0 && InpLongEnabledRegimes != "")
      return EnabledRegimeOk(InpLongEnabledRegimes);
   if(direction < 0 && InpShortEnabledRegimes != "")
      return EnabledRegimeOk(InpShortEnabledRegimes);
   return EntryRegimeOk();
}

bool EntryHourTextOk(string text, const datetime bar_time)
{
   StringToLower(text);
   StringTrimLeft(text);
   StringTrimRight(text);
   if(text == "" || text == "all" || text == "any" || text == "none")
      return true;

   MqlDateTime dt;
   TimeToStruct(bar_time + InpEntryHourShiftHours * 3600, dt);
   const int hour = dt.hour;

   string parts[];
   const int count = StringSplit(text, StringGetCharacter(",", 0), parts);
   for(int i = 0; i < count; i++)
   {
      string item = parts[i];
      StringTrimLeft(item);
      StringTrimRight(item);
      if(item == "")
         continue;

      const int dash = StringFind(item, "-");
      if(dash >= 0)
      {
         const int start_hour = (int)StringToInteger(StringSubstr(item, 0, dash));
         const int end_hour = (int)StringToInteger(StringSubstr(item, dash + 1));
         if(start_hour <= end_hour)
         {
            if(hour >= start_hour && hour <= end_hour)
               return true;
         }
         else if(hour >= start_hour || hour <= end_hour)
            return true;
      }
      else if(hour == (int)StringToInteger(item))
         return true;
   }
   return false;
}

bool EntryHourOk(const datetime bar_time)
{
   return EntryHourTextOk(InpEntryHoursUtc, bar_time);
}

bool EntryHourOk(const int direction, const datetime bar_time)
{
   if(direction > 0 && InpLongEntryHoursUtc != "")
      return EntryHourTextOk(InpLongEntryHoursUtc, bar_time);
   if(direction < 0 && InpShortEntryHoursUtc != "")
      return EntryHourTextOk(InpShortEntryHoursUtc, bar_time);
   return EntryHourOk(bar_time);
}

int ConfirmBarsFor(const int direction)
{
   const int configured = direction > 0 ? InpLongConfirmBars : InpShortConfirmBars;
   if(configured > 0)
      return configured;
   return MathMax(InpConfirmBars, 1);
}

int MinConfirmBars()
{
   return MathMin(ConfirmBarsFor(1), ConfirmBarsFor(-1));
}

bool SideConfirmOk(const int direction)
{
   return g_confirmed_regime != REGIME_UNKNOWN &&
          g_confirmed_regime_observed_count >= ConfirmBarsFor(direction);
}

bool EntryFiltersOk(const int direction, const datetime bar_time)
{
   return EntryRegimeOk(direction) &&
          EntryHourOk(direction, bar_time) &&
          SideConfirmOk(direction);
}

string RunModeToString()
{
   if(InpAllowTrading)
      return "ALLOW_TRADING";
   return "SIGNAL_ONLY";
}

string SanitizedToken(string value)
{
   StringReplace(value, "/", "_");
   StringReplace(value, "\\", "_");
   StringReplace(value, ":", "");
   StringReplace(value, ".", "");
   StringReplace(value, " ", "_");
   StringReplace(value, "-", "");
   return value;
}

string BuildRunId()
{
   string stamp = TimeToString(TimeLocal(), TIME_DATE | TIME_SECONDS);
   stamp = SanitizedToken(stamp);

   return StringFormat("%s_%s_%s_%s_%I64u",
                       stamp,
                       SanitizedToken(g_symbol),
                       TimeframeToString(InpTimeframe),
                       RunModeToString(),
                       InpMagicNumber);
}

string BuildLogFilename(const string filename)
{
   if(!InpUniqueLogFiles)
      return filename;

   int dot = StringFind(filename, ".csv");
   string base = filename;
   string ext = ".csv";
   if(dot >= 0)
   {
      base = StringSubstr(filename, 0, dot);
      ext = StringSubstr(filename, dot);
   }

   return StringFormat("%s_%s%s", base, g_run_id, ext);
}

void ConfigureLogFiles()
{
   g_run_id = BuildRunId();
   g_signal_log_file = BuildLogFilename(InpSignalLogFile);
   g_order_log_file = BuildLogFilename(InpOrderLogFile);
   g_error_log_file = BuildLogFilename(InpErrorLogFile);
}

string LogFolderDescription()
{
   if(InpUseCommonLogFolder)
      return TerminalInfoString(TERMINAL_COMMONDATA_PATH) + "\\Files";
   return TerminalInfoString(TERMINAL_DATA_PATH) + "\\MQL5\\Files";
}

bool EnsureSymbol()
{
   g_symbol = InpSymbol;
   if(g_symbol == "")
      g_symbol = _Symbol;

   if(!SymbolSelect(g_symbol, true))
   {
      PrintFormat("RegimeAdaptiveEA: symbol not available: %s", g_symbol);
      return false;
   }

   return true;
}

int OpenCsv(const string filename, const int flags)
{
   ResetLastError();
   int open_flags = flags | FILE_CSV | FILE_ANSI | FILE_SHARE_READ;
   if(InpUseCommonLogFolder)
      open_flags |= FILE_COMMON;

   int handle = FileOpen(filename, open_flags, ',');
   if(handle == INVALID_HANDLE)
      PrintFormat("RegimeAdaptiveEA: cannot open %s, error=%d", filename, GetLastError());
   return handle;
}

void PrepareCsv(const string filename, const string &header[])
{
   int flags = FILE_READ | FILE_WRITE;
   if(!InpAppendLog || InpUniqueLogFiles)
      flags = FILE_WRITE;

   int handle = OpenCsv(filename, flags);
   if(handle == INVALID_HANDLE)
      return;

   if(InpAppendLog && !InpUniqueLogFiles)
      FileSeek(handle, 0, SEEK_END);

   if(!InpAppendLog || InpUniqueLogFiles || FileTell(handle) == 0)
   {
      for(int i = 0; i < ArraySize(header); i++)
      {
         if(i == ArraySize(header) - 1)
            FileWriteString(handle, header[i]);
         else
            FileWriteString(handle, header[i] + ",");
      }
      FileWriteString(handle, "\r\n");
   }

   FileClose(handle);
}

void PrepareLogs()
{
   string signal_header[] = {
      "timestamp",
      "symbol",
      "timeframe",
      "run_mode",
      "price_mode",
      "close",
      "rolling_high",
      "rolling_low",
      "channel_width",
      "atr",
      "width_score",
      "range_pos",
      "trend_slope",
      "momentum",
      "short_ma",
      "prev_high",
      "prev_low",
      "raw_regime",
      "confirmed_regime",
      "signal",
      "position_before",
      "position_after",
      "position_bars",
      "own_positions",
      "unknown_positions",
      "cooldown_bars_remaining",
      "confirmed_regime_age",
      "spread_points"
   };
   string order_header[] = {
      "timestamp",
      "symbol",
      "action",
      "direction",
      "volume",
      "ticket",
      "success",
      "retcode",
      "retcode_description",
      "comment"
   };
   string error_header[] = {
      "timestamp",
      "symbol",
      "code",
      "message"
   };

   PrepareCsv(g_signal_log_file, signal_header);
   PrepareCsv(g_order_log_file, order_header);
   PrepareCsv(g_error_log_file, error_header);
}

void AppendErrorLog(const string code, const string message)
{
   int handle = OpenCsv(g_error_log_file, FILE_READ | FILE_WRITE);
   if(handle == INVALID_HANDLE)
      return;

   FileSeek(handle, 0, SEEK_END);
   FileWrite(handle,
             TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
             g_symbol,
             code,
             message);
   FileClose(handle);
}

void AppendOrderLog(const string action,
                    const string direction,
                    const double volume,
                    const ulong ticket,
                    const bool success,
                    const uint retcode,
                    const string retcode_description,
                    const string comment)
{
   int handle = OpenCsv(g_order_log_file, FILE_READ | FILE_WRITE);
   if(handle == INVALID_HANDLE)
      return;

   FileSeek(handle, 0, SEEK_END);
   FileWrite(handle,
             TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
             g_symbol,
             action,
             direction,
             DoubleToString(volume, 2),
             ticket,
             success ? "true" : "false",
             retcode,
             retcode_description,
             comment);
   FileClose(handle);
}

void AppendSignalLog(const datetime bar_time,
                     const FactorSnapshot &factors,
                     const ENUM_MARKET_REGIME raw_regime,
                     const string signal,
                     const int position_before,
                     const int position_after,
                     const int position_bars,
                     const PositionSnapshot &state)
{
   int handle = OpenCsv(g_signal_log_file, FILE_READ | FILE_WRITE);
   if(handle == INVALID_HANDLE)
      return;

   FileSeek(handle, 0, SEEK_END);
   const long spread_points = SymbolInfoInteger(g_symbol, SYMBOL_SPREAD);
   FileWrite(handle,
             TimeToString(bar_time, TIME_DATE | TIME_SECONDS),
             g_symbol,
             TimeframeToString(InpTimeframe),
             RunModeToString(),
             PriceModeToString(InpPriceMode),
             DoubleToString(factors.close, _Digits),
             DoubleToString(factors.rolling_high, _Digits),
             DoubleToString(factors.rolling_low, _Digits),
             DoubleToString(factors.channel_width, _Digits),
             DoubleToString(factors.atr, _Digits),
             DoubleToString(factors.width_score, 8),
             DoubleToString(factors.range_pos, 8),
             DoubleToString(factors.trend_slope, 8),
             DoubleToString(factors.momentum, 8),
             DoubleToString(factors.short_ma, _Digits),
             DoubleToString(factors.prev_high, _Digits),
             DoubleToString(factors.prev_low, _Digits),
             RegimeToString(raw_regime),
             RegimeToString(g_confirmed_regime),
             signal,
             DirectionToString(position_before),
             DirectionToString(position_after),
             position_bars,
             state.own_count,
             state.unknown_count,
             g_cooldown_bars_remaining,
             g_confirmed_regime_age,
             spread_points);
   FileClose(handle);
}

double TickMidNearBarClose(const MqlRates &bar)
{
   const long bar_close_msc = ((long)bar.time + PeriodSeconds(InpTimeframe)) * 1000;
   const long from_msc = bar_close_msc - 60000;

   MqlTick ticks[];
   const int copied = CopyTicksRange(g_symbol, ticks, COPY_TICKS_INFO, from_msc, bar_close_msc);
   for(int i = copied - 1; i >= 0; i--)
   {
      if(ticks[i].bid > 0.0 && ticks[i].ask > 0.0)
         return (ticks[i].bid + ticks[i].ask) / 2.0;
   }

   MqlTick tick;
   if(SymbolInfoTick(g_symbol, tick) && tick.bid > 0.0 && tick.ask > 0.0)
      return (tick.bid + tick.ask) / 2.0;

   return bar.close;
}

double SelectedClosePrice(const MqlRates &bar)
{
   if(InpPriceMode == PRICE_MID_FROM_TICK)
      return TickMidNearBarClose(bar);

   return bar.close;
}

int RequiredBars()
{
   int required = InpChannelLookback;
   required = MathMax(required, InpAtrLookback + 1);
   required = MathMax(required, InpTrendLookback);
   required = MathMax(required, InpMomentumLookback + 1);
   required = MathMax(required, InpBreakoutLookback + 1);
   required = MathMax(required, InpShortMaLookback);
   return required;
}

bool LoadWindow(MqlRates &rates[], const int required)
{
   ArraySetAsSeries(rates, true);
   const int copied = CopyRates(g_symbol, InpTimeframe, 1, required, rates);
   if(copied < required)
   {
      const string message = StringFormat("waiting for complete bars, copied=%d required=%d", copied, required);
      PrintFormat("RegimeAdaptiveEA: %s", message);
      AppendErrorLog("BAR_HISTORY_INCOMPLETE", message);
      return false;
   }

   const int period_seconds = PeriodSeconds(InpTimeframe);
   if(period_seconds <= 0 || rates[0].time + period_seconds > TimeCurrent())
   {
      AppendErrorLog("BAR_NOT_COMPLETE", "latest closed bar appears incomplete");
      return false;
   }

   return true;
}

bool BuildCloseWindow(const MqlRates &rates[], const int required, double &closes[])
{
   ArrayResize(closes, required);
   for(int i = 0; i < required; i++)
      closes[i] = SelectedClosePrice(rates[i]);
   return true;
}

double LinearRegressionSlope(const double &closes[], const int start, const int count)
{
   if(count <= 1)
      return 0.0;

   double x_sum = 0.0;
   double y_sum = 0.0;
   for(int i = 0; i < count; i++)
   {
      x_sum += i;
      y_sum += closes[start + (count - 1 - i)];
   }

   const double x_mean = x_sum / count;
   const double y_mean = y_sum / count;
   double numerator = 0.0;
   double denominator = 0.0;
   for(int i = 0; i < count; i++)
   {
      const double x = i;
      const double y = closes[start + (count - 1 - i)];
      numerator += (x - x_mean) * (y - y_mean);
      denominator += MathPow(x - x_mean, 2.0);
   }

   if(denominator <= 0.0)
      return 0.0;
   return numerator / denominator;
}

bool BuildFactors(const MqlRates &rates[], const double &closes[], FactorSnapshot &factors)
{
   factors.close = closes[0];
   factors.rolling_high = rates[0].high;
   factors.rolling_low = rates[0].low;
   for(int i = 0; i < InpChannelLookback; i++)
   {
      factors.rolling_high = MathMax(factors.rolling_high, rates[i].high);
      factors.rolling_low = MathMin(factors.rolling_low, rates[i].low);
   }
   factors.channel_width = factors.rolling_high - factors.rolling_low;

   double tr_sum = 0.0;
   for(int i = 0; i < InpAtrLookback; i++)
   {
      const double prev_close = closes[i + 1];
      const double true_range = MathMax(rates[i].high - rates[i].low,
                                MathMax(MathAbs(rates[i].high - prev_close),
                                        MathAbs(rates[i].low - prev_close)));
      tr_sum += true_range;
   }
   factors.atr = tr_sum / InpAtrLookback;
   factors.width_score = factors.channel_width / MathMax(factors.atr, 1e-12);
   factors.range_pos = (factors.close - factors.rolling_low) / MathMax(factors.channel_width, 1e-12);
   factors.trend_slope = LinearRegressionSlope(closes, 0, InpTrendLookback) / MathMax(factors.atr, 1e-12);
   factors.momentum = factors.close / closes[InpMomentumLookback] - 1.0;

   double ma_sum = 0.0;
   for(int i = 0; i < InpShortMaLookback; i++)
      ma_sum += closes[i];
   factors.short_ma = ma_sum / InpShortMaLookback;

   factors.prev_high = rates[1].high;
   factors.prev_low = rates[1].low;
   for(int i = 1; i <= InpBreakoutLookback; i++)
   {
      factors.prev_high = MathMax(factors.prev_high, rates[i].high);
      factors.prev_low = MathMin(factors.prev_low, rates[i].low);
   }

   return true;
}

ENUM_MARKET_REGIME ClassifyRegime(const FactorSnapshot &factors)
{
   if(factors.trend_slope > InpBullThreshold)
   {
      if(factors.width_score <= InpNarrowWidthThreshold)
         return REGIME_NARROW_BULL;
      return REGIME_WIDE_BULL;
   }
   if(MathAbs(factors.trend_slope) <= InpFlatThreshold && factors.width_score >= InpWideRangeThreshold)
      return REGIME_WIDE_RANGE;
   if(factors.trend_slope < -InpBearThreshold)
   {
      if(factors.width_score <= InpNarrowWidthThreshold)
         return REGIME_NARROW_BEAR;
      return REGIME_WIDE_BEAR;
   }
   return REGIME_UNKNOWN;
}

bool ConfirmRegime(const ENUM_MARKET_REGIME raw_regime)
{
   if(raw_regime == REGIME_UNKNOWN)
   {
      g_pending_regime = REGIME_UNKNOWN;
      g_pending_regime_count = 0;
      return false;
   }

   if(raw_regime == g_pending_regime)
      g_pending_regime_count++;
   else
   {
      g_pending_regime = raw_regime;
      g_pending_regime_count = 1;
   }

   if(g_pending_regime_count >= MinConfirmBars() && raw_regime != g_confirmed_regime)
   {
      g_confirmed_regime = raw_regime;
      g_confirmed_regime_age = 0;
      g_confirmed_regime_observed_count = g_pending_regime_count;
      return true;
   }

   if(raw_regime == g_confirmed_regime)
   {
      g_confirmed_regime_age++;
      g_confirmed_regime_observed_count = MathMax(g_confirmed_regime_observed_count,
                                                  g_pending_regime_count);
   }

   return false;
}

void ResetPositionSnapshot(PositionSnapshot &state)
{
   state.own_count = 0;
   state.unknown_count = 0;
   state.direction = 0;
   state.ticket = 0;
   state.volume = 0.0;
   state.open_time = 0;
   state.type = -1;
}

bool LoadPositionSnapshot(PositionSnapshot &state)
{
   ResetPositionSnapshot(state);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      const ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;

      const string symbol = PositionGetString(POSITION_SYMBOL);
      if(symbol != g_symbol)
         continue;

      const long magic = PositionGetInteger(POSITION_MAGIC);
      if((ulong)magic != InpMagicNumber)
      {
         state.unknown_count++;
         continue;
      }

      state.own_count++;
      if(state.ticket == 0)
      {
         state.ticket = ticket;
         state.volume = PositionGetDouble(POSITION_VOLUME);
         state.open_time = (datetime)PositionGetInteger(POSITION_TIME);
         state.type = PositionGetInteger(POSITION_TYPE);
         if(state.type == POSITION_TYPE_BUY)
            state.direction = 1;
         else if(state.type == POSITION_TYPE_SELL)
            state.direction = -1;
      }
   }

   return true;
}

int BarsSinceOpen(const PositionSnapshot &state, const datetime bar_time)
{
   if(state.open_time <= 0)
      return 0;

   const int open_shift = iBarShift(g_symbol, InpTimeframe, state.open_time, false);
   const int bar_shift = iBarShift(g_symbol, InpTimeframe, bar_time, false);
   if(open_shift >= 0 && bar_shift >= 0 && open_shift >= bar_shift)
      return open_shift - bar_shift;

   return 0;
}

double NormalizeVolume(const double requested_volume)
{
   const double min_volume = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   const double max_volume = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MAX);
   const double step = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);

   if(step <= 0.0)
      return requested_volume;

   double volume = MathMax(min_volume, MathMin(max_volume, requested_volume));
   volume = MathFloor(volume / step) * step;
   const int volume_digits = (int)MathMax(0, MathRound(-MathLog10(step)));
   return NormalizeDouble(volume, volume_digits);
}

bool TradingEnvironmentOk(const string action, const int direction, const PositionSnapshot &state)
{
   if(!InpAllowTrading)
      return false;

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      AppendErrorLog("TRADE_DISABLED_TERMINAL", "terminal algo trading is disabled");
      return false;
   }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      AppendErrorLog("TRADE_DISABLED_EA", "EA live trading permission is disabled");
      return false;
   }
   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
   {
      AppendErrorLog("TRADE_DISABLED_ACCOUNT", "account trading is disabled");
      return false;
   }

   if(state.unknown_count > 0)
   {
      AppendErrorLog("UNKNOWN_POSITION", "found non-EA position on symbol, refusing to trade");
      return false;
   }
   if(state.own_count > 1)
   {
      AppendErrorLog("MULTIPLE_EA_POSITIONS", "found more than one EA position on symbol, refusing to trade");
      return false;
   }

   const long spread_points = SymbolInfoInteger(g_symbol, SYMBOL_SPREAD);
   if(InpMaxSpreadPoints > 0 && spread_points > InpMaxSpreadPoints)
   {
      AppendErrorLog("SPREAD_TOO_WIDE", StringFormat("spread=%d max=%d", spread_points, InpMaxSpreadPoints));
      return false;
   }

   const long trade_mode = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_MODE);
   if(trade_mode == SYMBOL_TRADE_MODE_DISABLED)
   {
      AppendErrorLog("SYMBOL_TRADE_DISABLED", "symbol trade mode is disabled");
      return false;
   }
   if(action == "OPEN" && direction > 0 && trade_mode == SYMBOL_TRADE_MODE_SHORTONLY)
   {
      AppendErrorLog("SYMBOL_BUY_BLOCKED", "symbol is short-only");
      return false;
   }
   if(action == "OPEN" && direction < 0 && trade_mode == SYMBOL_TRADE_MODE_LONGONLY)
   {
      AppendErrorLog("SYMBOL_SELL_BLOCKED", "symbol is long-only");
      return false;
   }
   if(action == "OPEN" && state.own_count != 0)
   {
      AppendErrorLog("OPEN_WHILE_POSITION_EXISTS", "open signal received while EA position exists");
      return false;
   }
   if(action == "CLOSE" && state.own_count != 1)
   {
      AppendErrorLog("CLOSE_WITHOUT_SINGLE_POSITION", "close signal received without exactly one EA position");
      return false;
   }

   const double volume = NormalizeVolume(InpLots);
   if(volume <= 0.0)
   {
      AppendErrorLog("INVALID_VOLUME", "normalized trade volume is not positive");
      return false;
   }

   return true;
}

void BuildStops(const int direction, double &sl, double &tp)
{
   sl = 0.0;
   tp = 0.0;

   MqlTick tick;
   if(!SymbolInfoTick(g_symbol, tick))
      return;

   const double point = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   if(point <= 0.0)
      return;

   const double entry_price = direction > 0 ? tick.ask : tick.bid;
   if(entry_price <= 0.0)
      return;

   if(InpStopLossPoints > 0)
      sl = direction > 0 ? entry_price - InpStopLossPoints * point : entry_price + InpStopLossPoints * point;
   if(InpTakeProfitPoints > 0)
      tp = direction > 0 ? entry_price + InpTakeProfitPoints * point : entry_price - InpTakeProfitPoints * point;

   sl = sl > 0.0 ? NormalizeDouble(sl, _Digits) : 0.0;
   tp = tp > 0.0 ? NormalizeDouble(tp, _Digits) : 0.0;
}

bool ExecuteOpen(const int direction, const string signal, const PositionSnapshot &state)
{
   if(!TradingEnvironmentOk("OPEN", direction, state))
      return false;

   const double volume = NormalizeVolume(InpLots);
   double sl = 0.0;
   double tp = 0.0;
   BuildStops(direction, sl, tp);

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(InpDeviationPoints);

   ResetLastError();
   bool success = false;
   if(direction > 0)
      success = g_trade.Buy(volume, g_symbol, 0.0, sl, tp, signal);
   else
      success = g_trade.Sell(volume, g_symbol, 0.0, sl, tp, signal);

   AppendOrderLog("OPEN",
                  DirectionToString(direction),
                  volume,
                  g_trade.ResultOrder(),
                  success,
                  g_trade.ResultRetcode(),
                  g_trade.ResultRetcodeDescription(),
                  signal);

   if(!success)
      AppendErrorLog("ORDER_OPEN_FAILED", g_trade.ResultRetcodeDescription());

   return success;
}

bool ExecuteClose(const string signal, const PositionSnapshot &state)
{
   if(!TradingEnvironmentOk("CLOSE", state.direction, state))
      return false;

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(InpDeviationPoints);

   ResetLastError();
   const bool success = g_trade.PositionClose(state.ticket, InpDeviationPoints);
   AppendOrderLog("CLOSE",
                  DirectionToString(state.direction),
                  state.volume,
                  state.ticket,
                  success,
                  g_trade.ResultRetcode(),
                  g_trade.ResultRetcodeDescription(),
                  signal);

   if(!success)
      AppendErrorLog("ORDER_CLOSE_FAILED", g_trade.ResultRetcodeDescription());

   return success;
}

bool HitLongStop(const double close, const double entry_price, const double entry_atr)
{
   const double stop_mult = InpLongStopAtrMult > 0.0 ? InpLongStopAtrMult : InpStopAtrMult;
   return entry_price > 0.0 && entry_atr > 0.0 && close <= entry_price - stop_mult * entry_atr;
}

bool HitShortStop(const double close, const double entry_price, const double entry_atr)
{
   const double stop_mult = InpShortStopAtrMult > 0.0 ? InpShortStopAtrMult : InpStopAtrMult;
   return entry_price > 0.0 && entry_atr > 0.0 && close >= entry_price + stop_mult * entry_atr;
}

bool HitMaxPositionBars(const int position_bars)
{
   return InpMaxPositionBars > 0 && position_bars >= InpMaxPositionBars;
}

double SpreadPriceEstimate()
{
   const double point = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   if(point <= 0.0)
      return 0.0;

   MqlTick tick;
   if(SymbolInfoTick(g_symbol, tick) && tick.ask > 0.0 && tick.bid > 0.0)
      return MathMax(tick.ask - tick.bid, 0.0);

   return MathMax(InpAssumedSpreadPoints, 0) * point;
}

double TargetDistance(const int direction, const FactorSnapshot &factors)
{
   if(direction > 0)
   {
      double target = factors.rolling_low + InpUpperThird * factors.channel_width;
      if(g_confirmed_regime == REGIME_NARROW_BULL)
         target = factors.rolling_high;
      return MathMax(target - factors.close, 0.0);
   }

   double target = factors.rolling_low + InpLowerThird * factors.channel_width;
   if(g_confirmed_regime == REGIME_NARROW_BEAR)
      target = factors.rolling_low;
   return MathMax(factors.close - target, 0.0);
}

bool TargetSpaceOk(const int direction, const FactorSnapshot &factors)
{
   double min_target_atr_mult = InpMinTargetAtrMult;
   if(direction > 0 && InpLongMinTargetAtrMult >= 0.0)
      min_target_atr_mult = InpLongMinTargetAtrMult;
   else if(direction < 0 && InpShortMinTargetAtrMult >= 0.0)
      min_target_atr_mult = InpShortMinTargetAtrMult;

   if(min_target_atr_mult <= 0.0 && InpMinTargetSpreadMult <= 0.0)
      return true;

   const double min_distance = MathMax(min_target_atr_mult * factors.atr,
                                       InpMinTargetSpreadMult * SpreadPriceEstimate());
   return TargetDistance(direction, factors) >= min_distance;
}

bool CanEnterLong(const FactorSnapshot &factors)
{
   if(InpDisableWideRangeLongs && g_confirmed_regime == REGIME_WIDE_RANGE)
      return false;
   if(!InpStrictLongFilter)
      return true;

   return factors.trend_slope > InpBullThreshold * InpStrictLongTrendMult &&
          factors.close > factors.short_ma &&
          factors.momentum > 0.0;
}

bool ShouldExitLong(const FactorSnapshot &factors,
                    const int position_bars,
                    const double entry_price,
                    const double entry_atr)
{
   if(HitLongStop(factors.close, entry_price, entry_atr) || HitMaxPositionBars(position_bars))
      return true;

   if(g_confirmed_regime == REGIME_NARROW_BULL)
      return factors.momentum <= 0.0 || factors.close < factors.short_ma;
   if(g_confirmed_regime == REGIME_WIDE_BULL)
      return factors.range_pos >= InpUpperThird || factors.trend_slope <= 0.0;
   if(g_confirmed_regime == REGIME_WIDE_RANGE)
      return factors.range_pos >= InpUpperThird || factors.close < factors.prev_low;
   return g_confirmed_regime == REGIME_WIDE_BEAR || g_confirmed_regime == REGIME_NARROW_BEAR;
}

bool ShouldExitShort(const FactorSnapshot &factors,
                     const int position_bars,
                     const double entry_price,
                     const double entry_atr)
{
   if(HitShortStop(factors.close, entry_price, entry_atr) || HitMaxPositionBars(position_bars))
      return true;

   if(g_confirmed_regime == REGIME_NARROW_BEAR)
      return factors.momentum >= 0.0 || factors.close > factors.short_ma;
   if(g_confirmed_regime == REGIME_WIDE_BEAR)
      return factors.range_pos <= InpLowerThird || factors.trend_slope >= 0.0;
   if(g_confirmed_regime == REGIME_WIDE_RANGE)
      return factors.range_pos <= InpLowerThird || factors.close > factors.prev_high;
   return g_confirmed_regime == REGIME_WIDE_BULL || g_confirmed_regime == REGIME_NARROW_BULL;
}

int EntryDirection(const FactorSnapshot &factors, const datetime bar_time)
{
   if(g_confirmed_regime_age < InpMinRegimeBars)
      return 0;

   if(g_confirmed_regime == REGIME_NARROW_BULL &&
      (factors.momentum > InpMomentumEntry || factors.close > factors.prev_high))
   {
      if(!EntryFiltersOk(1, bar_time))
         return 0;
      if(!CanEnterLong(factors))
         return 0;
      if(!TargetSpaceOk(1, factors))
         return 0;
      return 1;
   }
   if(g_confirmed_regime == REGIME_WIDE_BULL &&
      factors.range_pos <= InpPullbackBuyZone &&
      factors.trend_slope > InpFlatThreshold)
   {
      if(!EntryFiltersOk(1, bar_time))
         return 0;
      if(!CanEnterLong(factors))
         return 0;
      if(!TargetSpaceOk(1, factors))
         return 0;
      return 1;
   }
   if(g_confirmed_regime == REGIME_WIDE_RANGE &&
      factors.range_pos <= InpLowerThird &&
      factors.close >= factors.prev_low)
   {
      if(!EntryFiltersOk(1, bar_time))
         return 0;
      if(!CanEnterLong(factors))
         return 0;
      if(!TargetSpaceOk(1, factors))
         return 0;
      return 1;
   }
   if(g_confirmed_regime == REGIME_NARROW_BEAR &&
      (factors.momentum < -InpMomentumEntry || factors.close < factors.prev_low))
   {
      if(!EntryFiltersOk(-1, bar_time))
         return 0;
      if(!TargetSpaceOk(-1, factors))
         return 0;
      return -1;
   }
   if(g_confirmed_regime == REGIME_WIDE_BEAR &&
      factors.range_pos >= InpPullbackSellZone &&
      factors.trend_slope < -InpFlatThreshold)
   {
      if(!EntryFiltersOk(-1, bar_time))
         return 0;
      if(!TargetSpaceOk(-1, factors))
         return 0;
      return -1;
   }
   if(g_confirmed_regime == REGIME_WIDE_RANGE &&
      factors.range_pos >= InpUpperThird &&
      factors.close <= factors.prev_high)
   {
      if(!EntryFiltersOk(-1, bar_time))
         return 0;
      if(!TargetSpaceOk(-1, factors))
         return 0;
      return -1;
   }
   return 0;
}

bool ValidateHourFilterText(string text, const string name)
{
   StringToLower(text);
   StringTrimLeft(text);
   StringTrimRight(text);
   if(text == "" || text == "all" || text == "any" || text == "none")
      return true;

   string parts[];
   const int count = StringSplit(text, StringGetCharacter(",", 0), parts);
   for(int i = 0; i < count; i++)
   {
      string item = parts[i];
      StringTrimLeft(item);
      StringTrimRight(item);
      if(item == "")
         continue;

      const int dash = StringFind(item, "-");
      if(dash >= 0)
      {
         const int start_hour = (int)StringToInteger(StringSubstr(item, 0, dash));
         const int end_hour = (int)StringToInteger(StringSubstr(item, dash + 1));
         if(start_hour < 0 || start_hour > 23 || end_hour < 0 || end_hour > 23)
         {
            PrintFormat("RegimeAdaptiveEA: invalid %s range: %s", name, item);
            return false;
         }
      }
      else
      {
         const int hour = (int)StringToInteger(item);
         if(hour < 0 || hour > 23)
         {
            PrintFormat("RegimeAdaptiveEA: invalid %s hour: %s", name, item);
            return false;
         }
      }
   }
   return true;
}

bool ValidateHourFilter()
{
   return ValidateHourFilterText(InpEntryHoursUtc, "InpEntryHoursUtc") &&
          ValidateHourFilterText(InpLongEntryHoursUtc, "InpLongEntryHoursUtc") &&
          ValidateHourFilterText(InpShortEntryHoursUtc, "InpShortEntryHoursUtc");
}

int SignalOnlyPositionAfter(const string signal, const int position_before)
{
   if(signal == "ENTER_LONG")
      return 1;
   if(signal == "ENTER_SHORT")
      return -1;
   if(StringFind(signal, "EXIT_") == 0)
      return 0;
   return position_before;
}

void UpdateSignalOnlyState(const string signal, const FactorSnapshot &factors)
{
   const int before = g_virtual_position;
   g_virtual_position = SignalOnlyPositionAfter(signal, g_virtual_position);

   if(before == 0 && g_virtual_position != 0)
   {
      g_virtual_position_bars = 0;
      g_virtual_entry_price = factors.close;
      g_virtual_entry_atr = factors.atr;
   }
   else if(before != 0 && g_virtual_position == 0)
   {
      g_virtual_position_bars = 0;
      g_cooldown_bars_remaining = MathMax(InpCooldownBars, 0);
      g_virtual_entry_price = 0.0;
      g_virtual_entry_atr = 0.0;
   }
}

void ProcessClosedBar()
{
   const int required = RequiredBars();
   MqlRates rates[];
   if(!LoadWindow(rates, required))
      return;

   double closes[];
   if(!BuildCloseWindow(rates, required, closes))
      return;

   FactorSnapshot factors;
   if(!BuildFactors(rates, closes, factors))
      return;

   PositionSnapshot state;
   LoadPositionSnapshot(state);

   const datetime bar_time = rates[0].time;
   const ENUM_MARKET_REGIME raw_regime = ClassifyRegime(factors);
   const bool regime_changed = ConfirmRegime(raw_regime);

   int position_before = InpAllowTrading ? state.direction : g_virtual_position;
   int position_bars = 0;
   if(InpAllowTrading && state.direction != 0)
      position_bars = BarsSinceOpen(state, bar_time);
   else if(!InpAllowTrading && g_virtual_position != 0)
      position_bars = g_virtual_position_bars + 1;

   double entry_price = InpAllowTrading ? g_live_entry_price : g_virtual_entry_price;
   double entry_atr = InpAllowTrading ? g_live_entry_atr : g_virtual_entry_atr;

   string signal = "HOLD";
   int trade_action = 0; // 1 open long, -1 open short, 2 close.

   if(position_before == 0)
   {
      if(g_cooldown_bars_remaining > 0)
      {
         g_cooldown_bars_remaining--;
         signal = "COOLDOWN";
      }
      else if(regime_changed)
      {
         signal = "REGIME_CHANGE_WAIT";
      }
      else
      {
         const int entry_direction = EntryDirection(factors, bar_time);
         if(entry_direction > 0)
         {
            signal = "ENTER_LONG";
            trade_action = 1;
         }
         else if(entry_direction < 0)
         {
            signal = "ENTER_SHORT";
            trade_action = -1;
         }
      }
   }
   else
   {
      if(regime_changed)
      {
         signal = position_before > 0 ? "EXIT_LONG_REGIME_CHANGE" : "EXIT_SHORT_REGIME_CHANGE";
         trade_action = 2;
      }
      else if(position_before > 0 && ShouldExitLong(factors, position_bars, entry_price, entry_atr))
      {
         signal = "EXIT_LONG_RULE";
         trade_action = 2;
      }
      else if(position_before < 0 && ShouldExitShort(factors, position_bars, entry_price, entry_atr))
      {
         signal = "EXIT_SHORT_RULE";
         trade_action = 2;
      }
   }

   if(!InpAllowTrading && position_before != 0 && signal == "HOLD")
      g_virtual_position_bars = position_bars;

   if(InpAllowTrading && trade_action == 1)
   {
      if(ExecuteOpen(1, signal, state))
      {
         g_live_entry_price = factors.close;
         g_live_entry_atr = factors.atr;
      }
   }
   else if(InpAllowTrading && trade_action == -1)
   {
      if(ExecuteOpen(-1, signal, state))
      {
         g_live_entry_price = factors.close;
         g_live_entry_atr = factors.atr;
      }
   }
   else if(InpAllowTrading && trade_action == 2)
   {
      if(ExecuteClose(signal, state))
      {
         g_cooldown_bars_remaining = MathMax(InpCooldownBars, 0);
         g_live_entry_price = 0.0;
         g_live_entry_atr = 0.0;
      }
   }
   else if(!InpAllowTrading)
   {
      UpdateSignalOnlyState(signal, factors);
   }

   PositionSnapshot state_after;
   LoadPositionSnapshot(state_after);
   const int position_after = InpAllowTrading ? state_after.direction : g_virtual_position;
   AppendSignalLog(bar_time,
                   factors,
                   raw_regime,
                   signal,
                   position_before,
                   position_after,
                   position_bars,
                   state_after);

   PrintFormat("RegimeAdaptiveEA %s %s close=%s raw=%s confirmed=%s signal=%s mode=%s position=%s own=%d unknown=%d",
               g_symbol,
               TimeToString(bar_time, TIME_DATE | TIME_SECONDS),
               DoubleToString(factors.close, _Digits),
               RegimeToString(raw_regime),
               RegimeToString(g_confirmed_regime),
               signal,
               RunModeToString(),
               DirectionToString(position_after),
               state_after.own_count,
               state_after.unknown_count);
}

bool ValidateInputs()
{
   if(InpChannelLookback <= 1 || InpAtrLookback <= 1 || InpTrendLookback <= 1)
   {
      Print("RegimeAdaptiveEA: lookbacks must be greater than 1");
      return false;
   }
   if(InpMomentumLookback <= 0 || InpBreakoutLookback <= 0 || InpShortMaLookback <= 0)
   {
      Print("RegimeAdaptiveEA: momentum, breakout, and short MA lookbacks must be positive");
      return false;
   }
   if(InpConfirmBars <= 0)
   {
      Print("RegimeAdaptiveEA: InpConfirmBars must be positive");
      return false;
   }
   if(InpLongConfirmBars < 0 || InpShortConfirmBars < 0)
   {
      Print("RegimeAdaptiveEA: side confirm bars cannot be negative");
      return false;
   }
   if(InpCooldownBars < 0 || InpMinRegimeBars < 0)
   {
      Print("RegimeAdaptiveEA: cooldown and min regime bars cannot be negative");
      return false;
   }
   if(InpLots <= 0.0)
   {
      Print("RegimeAdaptiveEA: InpLots must be positive");
      return false;
   }
   if(InpPullbackBuyZone <= 0.0 || InpPullbackSellZone <= 0.0 || InpLowerThird <= 0.0 || InpUpperThird <= 0.0)
   {
      Print("RegimeAdaptiveEA: range zones must be positive");
      return false;
   }
   if(InpLowerThird >= InpUpperThird)
   {
      Print("RegimeAdaptiveEA: InpLowerThird must be lower than InpUpperThird");
      return false;
   }
   if(InpEntryHourShiftHours < -23 || InpEntryHourShiftHours > 23)
   {
      Print("RegimeAdaptiveEA: InpEntryHourShiftHours must be between -23 and 23");
      return false;
   }
   if(!ValidateHourFilter())
      return false;
   return true;
}

int OnInit()
{
   if(!ValidateInputs())
      return INIT_PARAMETERS_INCORRECT;

   if(!EnsureSymbol())
      return INIT_FAILED;

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(InpDeviationPoints);
   g_last_closed_bar_time = 0;
   g_virtual_position = 0;
   g_virtual_position_bars = 0;
   g_cooldown_bars_remaining = 0;
   g_virtual_entry_price = 0.0;
   g_virtual_entry_atr = 0.0;
   g_confirmed_regime = REGIME_UNKNOWN;
   g_pending_regime = REGIME_UNKNOWN;
   g_pending_regime_count = 0;
   g_confirmed_regime_age = 0;
   g_confirmed_regime_observed_count = 0;
   g_live_entry_price = 0.0;
   g_live_entry_atr = 0.0;
   ConfigureLogFiles();
   PrepareLogs();

   PrintFormat("RegimeAdaptiveEA initialized: symbol=%s timeframe=%s channel=%d atr=%d trend=%d confirm=%d long_confirm=%d short_confirm=%d mode=%s lots=%.2f magic=%I64u max_spread=%d price_mode=%s disabled_regimes=%s entry_hours_utc=%s long_regimes=%s short_regimes=%s long_hours_utc=%s short_hours_utc=%s entry_hour_shift=%d",
               g_symbol,
               TimeframeToString(InpTimeframe),
               InpChannelLookback,
               InpAtrLookback,
               InpTrendLookback,
               InpConfirmBars,
               InpLongConfirmBars,
               InpShortConfirmBars,
               RunModeToString(),
               InpLots,
               InpMagicNumber,
               InpMaxSpreadPoints,
               PriceModeToString(InpPriceMode),
               InpDisabledEntryRegimes,
               InpEntryHoursUtc,
               InpLongEnabledRegimes,
               InpShortEnabledRegimes,
               InpLongEntryHoursUtc,
               InpShortEntryHoursUtc,
               InpEntryHourShiftHours);
   PrintFormat("RegimeAdaptiveEA logs: folder=%s signal=%s order=%s error=%s",
               LogFolderDescription(),
               g_signal_log_file,
               g_order_log_file,
               g_error_log_file);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(InpAllowTrading && InpCloseOnDeinit)
   {
      PositionSnapshot state;
      LoadPositionSnapshot(state);
      if(state.own_count == 1 && state.unknown_count == 0)
         ExecuteClose("CLOSE_ON_DEINIT", state);
   }

   PrintFormat("RegimeAdaptiveEA stopped: reason=%d final_virtual_position=%s confirmed_regime=%s",
               reason,
               DirectionToString(g_virtual_position),
               RegimeToString(g_confirmed_regime));
}

void OnTick()
{
   if(g_symbol == "" && !EnsureSymbol())
      return;

   const datetime closed_bar_time = iTime(g_symbol, InpTimeframe, 1);
   if(closed_bar_time == 0 || closed_bar_time == g_last_closed_bar_time)
      return;

   g_last_closed_bar_time = closed_bar_time;
   ProcessClosedBar();
}
