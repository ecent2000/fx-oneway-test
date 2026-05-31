#property copyright "FX Oneway Factor Test"
#property link      "https://localhost"
#property version   "2.00"
#property strict

#include <Trade/Trade.mqh>

enum ENUM_SIGNAL_PRICE_MODE
{
   PRICE_BID_BAR_CLOSE = 0,
   PRICE_MID_FROM_TICK = 1
};

input string                 InpSymbol = "";
input ENUM_TIMEFRAMES        InpTimeframe = PERIOD_M15;
input int                    InpLookback = 96;
input double                 InpEntryZ = 1.5;
input double                 InpExitZ = 0.2;
input double                 InpStopZ = 0.0;
input int                    InpMaxPositionBars = 0;
input ENUM_SIGNAL_PRICE_MODE InpPriceMode = PRICE_BID_BAR_CLOSE;

input bool                   InpAllowTrading = false;
input double                 InpLots = 0.01;
input ulong                  InpMagicNumber = 26053101;
input int                    InpMaxSpreadPoints = 30;
input int                    InpDeviationPoints = 20;
input int                    InpStopLossPoints = 0;
input int                    InpTakeProfitPoints = 0;
input bool                   InpCloseOnDeinit = false;

input string                 InpSignalLogFile = "rolling_zscore_signal_log.csv";
input string                 InpOrderLogFile = "rolling_zscore_order_log.csv";
input string                 InpErrorLogFile = "rolling_zscore_error_log.csv";
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

CTrade  g_trade;
string  g_symbol = "";
string  g_run_id = "";
string  g_signal_log_file = "";
string  g_order_log_file = "";
string  g_error_log_file = "";
datetime g_last_closed_bar_time = 0;
int     g_virtual_position = 0;
int     g_virtual_position_bars = 0;

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
      PrintFormat("RollingZScoreEA: symbol not available: %s", g_symbol);
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
      PrintFormat("RollingZScoreEA: cannot open %s, error=%d", filename, GetLastError());
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
      "mean",
      "std",
      "z_score",
      "signal",
      "position_before",
      "position_after",
      "position_bars",
      "own_positions",
      "unknown_positions",
      "entry_z",
      "exit_z",
      "stop_z",
      "max_position_bars",
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
                     const double close,
                     const double mean,
                     const double std,
                     const double z_score,
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
             DoubleToString(close, _Digits),
             DoubleToString(mean, _Digits),
             DoubleToString(std, _Digits),
             DoubleToString(z_score, 8),
             signal,
             DirectionToString(position_before),
             DirectionToString(position_after),
             position_bars,
             state.own_count,
             state.unknown_count,
             DoubleToString(InpEntryZ, 4),
             DoubleToString(InpExitZ, 4),
             DoubleToString(InpStopZ, 4),
             InpMaxPositionBars,
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

bool LoadWindow(MqlRates &rates[])
{
   ArraySetAsSeries(rates, true);
   const int copied = CopyRates(g_symbol, InpTimeframe, 1, InpLookback, rates);
   if(copied < InpLookback)
   {
      const string message = StringFormat("waiting for complete bars, copied=%d lookback=%d", copied, InpLookback);
      PrintFormat("RollingZScoreEA: %s", message);
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

bool BuildCloseWindow(const MqlRates &rates[], double &closes[])
{
   ArrayResize(closes, InpLookback);
   for(int i = 0; i < InpLookback; i++)
      closes[i] = SelectedClosePrice(rates[i]);
   return true;
}

double Mean(const double &values[])
{
   double sum = 0.0;
   for(int i = 0; i < InpLookback; i++)
      sum += values[i];
   return sum / InpLookback;
}

double StdDev(const double &values[], const double mean)
{
   double sum_sq = 0.0;
   for(int i = 0; i < InpLookback; i++)
      sum_sq += MathPow(values[i] - mean, 2.0);
   return MathSqrt(sum_sq / InpLookback);
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

void UpdateSignalOnlyState(const string signal)
{
   const int before = g_virtual_position;
   g_virtual_position = SignalOnlyPositionAfter(signal, g_virtual_position);

   if(before == 0 && g_virtual_position != 0)
      g_virtual_position_bars = 0;
   else if(before != 0 && g_virtual_position == 0)
      g_virtual_position_bars = 0;
}

void ProcessClosedBar()
{
   MqlRates rates[];
   if(!LoadWindow(rates))
      return;

   double closes[];
   if(!BuildCloseWindow(rates, closes))
      return;

   PositionSnapshot state;
   LoadPositionSnapshot(state);

   const datetime bar_time = rates[0].time;
   const double close = closes[0];
   const double mean = Mean(closes);
   const double std = StdDev(closes, mean);
   const double z_score = (std > 0.0) ? (close - mean) / std : 0.0;

   int position_before = InpAllowTrading ? state.direction : g_virtual_position;
   int position_bars = 0;
   if(InpAllowTrading && state.direction != 0)
      position_bars = BarsSinceOpen(state, bar_time);
   else if(!InpAllowTrading && g_virtual_position != 0)
      position_bars = g_virtual_position_bars + 1;

   string signal = "HOLD";
   int trade_action = 0; // 1 open long, -1 open short, 2 close.

   if(position_before == 0)
   {
      if(z_score < -InpEntryZ)
      {
         signal = "ENTER_LONG";
         trade_action = 1;
      }
      else if(z_score > InpEntryZ)
      {
         signal = "ENTER_SHORT";
         trade_action = -1;
      }
   }
   else if(position_before > 0)
   {
      if(z_score > -InpExitZ)
      {
         signal = "EXIT_LONG_MEAN_REVERT";
         trade_action = 2;
      }
      else if(InpStopZ > 0.0 && z_score < -InpStopZ)
      {
         signal = "EXIT_LONG_STOP_Z";
         trade_action = 2;
      }
      else if(InpMaxPositionBars > 0 && position_bars >= InpMaxPositionBars)
      {
         signal = "EXIT_LONG_MAX_BARS";
         trade_action = 2;
      }
   }
   else if(position_before < 0)
   {
      if(z_score < InpExitZ)
      {
         signal = "EXIT_SHORT_MEAN_REVERT";
         trade_action = 2;
      }
      else if(InpStopZ > 0.0 && z_score > InpStopZ)
      {
         signal = "EXIT_SHORT_STOP_Z";
         trade_action = 2;
      }
      else if(InpMaxPositionBars > 0 && position_bars >= InpMaxPositionBars)
      {
         signal = "EXIT_SHORT_MAX_BARS";
         trade_action = 2;
      }
   }

   if(!InpAllowTrading && position_before != 0 && signal == "HOLD")
      g_virtual_position_bars = position_bars;

   if(InpAllowTrading && trade_action == 1)
      ExecuteOpen(1, signal, state);
   else if(InpAllowTrading && trade_action == -1)
      ExecuteOpen(-1, signal, state);
   else if(InpAllowTrading && trade_action == 2)
      ExecuteClose(signal, state);
   else if(!InpAllowTrading)
      UpdateSignalOnlyState(signal);

   PositionSnapshot state_after;
   LoadPositionSnapshot(state_after);

   const int position_after = InpAllowTrading ? state_after.direction : g_virtual_position;
   AppendSignalLog(bar_time,
                   close,
                   mean,
                   std,
                   z_score,
                   signal,
                   position_before,
                   position_after,
                   position_bars,
                   state_after);

   PrintFormat("RollingZScoreEA %s %s close=%s z=%.5f signal=%s mode=%s position=%s own=%d unknown=%d",
               g_symbol,
               TimeToString(bar_time, TIME_DATE | TIME_SECONDS),
               DoubleToString(close, _Digits),
               z_score,
               signal,
               RunModeToString(),
               DirectionToString(position_after),
               state_after.own_count,
               state_after.unknown_count);
}

int OnInit()
{
   if(InpLookback <= 1)
   {
      Print("RollingZScoreEA: InpLookback must be greater than 1");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpEntryZ <= 0.0)
   {
      Print("RollingZScoreEA: InpEntryZ must be positive");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpLots <= 0.0)
   {
      Print("RollingZScoreEA: InpLots must be positive");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(!EnsureSymbol())
      return INIT_FAILED;

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(InpDeviationPoints);
   g_last_closed_bar_time = 0;
   g_virtual_position = 0;
   g_virtual_position_bars = 0;
   ConfigureLogFiles();
   PrepareLogs();

   PrintFormat("RollingZScoreEA initialized: symbol=%s timeframe=%s lookback=%d entry_z=%.4f exit_z=%.4f mode=%s lots=%.2f magic=%I64u max_spread=%d price_mode=%s",
               g_symbol,
               TimeframeToString(InpTimeframe),
               InpLookback,
               InpEntryZ,
               InpExitZ,
               RunModeToString(),
               InpLots,
               InpMagicNumber,
               InpMaxSpreadPoints,
               PriceModeToString(InpPriceMode));
   PrintFormat("RollingZScoreEA logs: folder=%s signal=%s order=%s error=%s",
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

   PrintFormat("RollingZScoreEA stopped: reason=%d final_virtual_position=%s",
               reason,
               DirectionToString(g_virtual_position));
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
