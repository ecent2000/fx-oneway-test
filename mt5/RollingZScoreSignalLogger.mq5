#property copyright "FX Oneway Factor Test"
#property link      "https://localhost"
#property version   "1.00"
#property strict

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
input string                 InpSignalLogFile = "rolling_zscore_signal_log.csv";
input bool                   InpAppendLog = true;

string   g_symbol = "";
datetime g_last_closed_bar_time = 0;
int      g_virtual_position = 0; // 1 long, -1 short, 0 flat.
int      g_position_bars = 0;

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

string PositionToString(const int position)
{
   if(position > 0)
      return "LONG";
   if(position < 0)
      return "SHORT";
   return "FLAT";
}

bool EnsureSymbol()
{
   g_symbol = InpSymbol;
   if(g_symbol == "")
      g_symbol = _Symbol;

   if(!SymbolSelect(g_symbol, true))
   {
      PrintFormat("RollingZScoreSignalLogger: symbol not available: %s", g_symbol);
      return false;
   }

   return true;
}

int OpenSignalLog(const int flags)
{
   int handle = FileOpen(InpSignalLogFile, flags | FILE_CSV | FILE_ANSI | FILE_SHARE_READ, ',');
   if(handle == INVALID_HANDLE)
      PrintFormat("RollingZScoreSignalLogger: cannot open log file %s, error=%d", InpSignalLogFile, GetLastError());
   return handle;
}

void WriteHeaderIfNeeded()
{
   int flags = FILE_READ | FILE_WRITE;
   if(!InpAppendLog)
      flags = FILE_WRITE;

   int handle = OpenSignalLog(flags);
   if(handle == INVALID_HANDLE)
      return;

   if(InpAppendLog)
      FileSeek(handle, 0, SEEK_END);

   if(!InpAppendLog || FileTell(handle) == 0)
   {
      FileWrite(handle,
                "timestamp",
                "symbol",
                "timeframe",
                "price_mode",
                "close",
                "mean",
                "std",
                "z_score",
                "signal",
                "position_before",
                "position_after",
                "position_bars",
                "entry_z",
                "exit_z",
                "stop_z",
                "max_position_bars",
                "spread_points");
   }

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

double LatestClose(const double &values[])
{
   return values[0];
}

bool LoadWindow(MqlRates &rates[])
{
   ArraySetAsSeries(rates, true);
   const int copied = CopyRates(g_symbol, InpTimeframe, 1, InpLookback, rates);
   if(copied < InpLookback)
   {
      PrintFormat("RollingZScoreSignalLogger: waiting for bars, copied=%d lookback=%d", copied, InpLookback);
      return false;
   }

   return true;
}

void AppendSignalLog(const datetime bar_time,
                     const double close,
                     const double mean,
                     const double std,
                     const double z_score,
                     const string signal,
                     const int position_before,
                     const int position_after)
{
   int handle = OpenSignalLog(FILE_READ | FILE_WRITE);
   if(handle == INVALID_HANDLE)
      return;

   FileSeek(handle, 0, SEEK_END);
   const long spread_points = SymbolInfoInteger(g_symbol, SYMBOL_SPREAD);
   FileWrite(handle,
             TimeToString(bar_time, TIME_DATE | TIME_SECONDS),
             g_symbol,
             TimeframeToString(InpTimeframe),
             PriceModeToString(InpPriceMode),
             DoubleToString(close, _Digits),
             DoubleToString(mean, _Digits),
             DoubleToString(std, _Digits),
             DoubleToString(z_score, 8),
             signal,
             PositionToString(position_before),
             PositionToString(position_after),
             g_position_bars,
             DoubleToString(InpEntryZ, 4),
             DoubleToString(InpExitZ, 4),
             DoubleToString(InpStopZ, 4),
             InpMaxPositionBars,
             spread_points);
   FileClose(handle);
}

void ProcessClosedBar()
{
   MqlRates rates[];
   if(!LoadWindow(rates))
      return;

   double closes[];
   if(!BuildCloseWindow(rates, closes))
      return;

   const datetime bar_time = rates[0].time;
   const double close = LatestClose(closes);
   const double mean = Mean(closes);
   const double std = StdDev(closes, mean);
   const double z_score = (std > 0.0) ? (close - mean) / std : 0.0;

   const int position_before = g_virtual_position;
   string signal = "HOLD";

   if(g_virtual_position == 0)
   {
      g_position_bars = 0;
      if(z_score < -InpEntryZ)
      {
         g_virtual_position = 1;
         signal = "ENTER_LONG";
      }
      else if(z_score > InpEntryZ)
      {
         g_virtual_position = -1;
         signal = "ENTER_SHORT";
      }
   }
   else if(g_virtual_position > 0)
   {
      g_position_bars++;
      if(z_score > -InpExitZ)
      {
         g_virtual_position = 0;
         signal = "EXIT_LONG_MEAN_REVERT";
      }
      else if(InpStopZ > 0.0 && z_score < -InpStopZ)
      {
         g_virtual_position = 0;
         signal = "EXIT_LONG_STOP_Z";
      }
      else if(InpMaxPositionBars > 0 && g_position_bars >= InpMaxPositionBars)
      {
         g_virtual_position = 0;
         signal = "EXIT_LONG_MAX_BARS";
      }
   }
   else if(g_virtual_position < 0)
   {
      g_position_bars++;
      if(z_score < InpExitZ)
      {
         g_virtual_position = 0;
         signal = "EXIT_SHORT_MEAN_REVERT";
      }
      else if(InpStopZ > 0.0 && z_score > InpStopZ)
      {
         g_virtual_position = 0;
         signal = "EXIT_SHORT_STOP_Z";
      }
      else if(InpMaxPositionBars > 0 && g_position_bars >= InpMaxPositionBars)
      {
         g_virtual_position = 0;
         signal = "EXIT_SHORT_MAX_BARS";
      }
   }

   if(g_virtual_position == 0 && position_before != 0)
      g_position_bars = 0;

   AppendSignalLog(bar_time, close, mean, std, z_score, signal, position_before, g_virtual_position);
   PrintFormat("RollingZScoreSignalLogger %s %s close=%s z=%.5f signal=%s position=%s",
               g_symbol,
               TimeToString(bar_time, TIME_DATE | TIME_SECONDS),
               DoubleToString(close, _Digits),
               z_score,
               signal,
               PositionToString(g_virtual_position));
}

int OnInit()
{
   if(InpLookback <= 1)
   {
      Print("RollingZScoreSignalLogger: InpLookback must be greater than 1");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpEntryZ <= 0.0)
   {
      Print("RollingZScoreSignalLogger: InpEntryZ must be positive");
      return INIT_PARAMETERS_INCORRECT;
   }

   if(!EnsureSymbol())
      return INIT_FAILED;

   g_last_closed_bar_time = 0;
   g_virtual_position = 0;
   g_position_bars = 0;
   WriteHeaderIfNeeded();

   PrintFormat("RollingZScoreSignalLogger initialized: symbol=%s timeframe=%s lookback=%d entry_z=%.4f exit_z=%.4f price_mode=%s log=%s",
               g_symbol,
               TimeframeToString(InpTimeframe),
               InpLookback,
               InpEntryZ,
               InpExitZ,
               PriceModeToString(InpPriceMode),
               InpSignalLogFile);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   PrintFormat("RollingZScoreSignalLogger stopped: reason=%d final_virtual_position=%s",
               reason,
               PositionToString(g_virtual_position));
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
