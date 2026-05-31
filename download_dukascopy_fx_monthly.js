#!/usr/bin/env node
'use strict';

const fs = require('fs');
const fsp = require('fs/promises');
const os = require('os');
const path = require('path');

const PAIRS = ['eurusd', 'usdjpy', 'gbpusd', 'audusd', 'usdcad', 'usdchf', 'nzdusd'];
const DEFAULT_TIMEFRAME = 'm1';
const DEFAULT_CONCURRENCY = 3;
const DEFAULT_MONTHS = 12;

const DUKASCOPY_NODE_PATH = path.join(
  process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'),
  'npm',
  'node_modules',
  'dukascopy-node'
);

const args = parseArgs(process.argv.slice(2));
const concurrency = readPositiveInteger(args.concurrency, DEFAULT_CONCURRENCY);
const monthsToDownload = readPositiveInteger(args.months, DEFAULT_MONTHS);
const pairs = args.pairs ? args.pairs.split(',').map((item) => item.trim().toLowerCase()).filter(Boolean) : PAIRS;
const timeframe = args.timeframe || DEFAULT_TIMEFRAME;
const outputRoot = path.resolve(__dirname, args['output-root'] || path.join('data', `dukascopy_fx_${timeframe}`));
const cacheRoot = path.resolve(__dirname, args['cache-root'] || path.join('data', `dukascopy_fx_${timeframe}_cache`));
const dailyRoot = path.join(outputRoot, 'daily');
const monthlyRoot = path.join(outputRoot, 'monthly');
const progressPath = path.join(outputRoot, 'progress.json');

let progress = {
  updatedAt: null,
  settings: {},
  tasks: {},
  monthly: {}
};
let progressWriteQueue = Promise.resolve();

main().catch((error) => {
  clearProgressLine();
  console.error('[fatal]', formatError(error));
  process.exitCode = 1;
});

async function main() {
  const { getHistoricalRates } = requireDukascopyNode();
  const months = args.from || args.to ? getMonthsInRange(args.from, args.to) : getLastCompleteMonths(monthsToDownload);
  const days = getDaysFromMonths(months);
  const tasks = buildDailyTasks(pairs, days);
  const startedAt = new Date();

  await fsp.mkdir(dailyRoot, { recursive: true });
  await fsp.mkdir(monthlyRoot, { recursive: true });
  await fsp.mkdir(cacheRoot, { recursive: true });
  progress = await loadProgress();
  progress.monthly = progress.monthly && typeof progress.monthly === 'object' ? progress.monthly : {};
  progress.settings = {
    pairs,
    months: months.map((month) => month.label),
    days: days.map((day) => day.label),
    timeframe,
    concurrency,
    outputRoot,
    dailyRoot,
    monthlyRoot,
    cacheRoot,
    dukascopyNodePath: DUKASCOPY_NODE_PATH
  };
  await saveProgress();

  console.log('Dukascopy FX daily downloader');
  console.log(`pairs: ${pairs.join(', ')}`);
  console.log(`days: ${days[0].label} .. ${days[days.length - 1].label}`);
  console.log(`timeframe: ${timeframe}`);
  console.log(`concurrency: ${concurrency}`);
  console.log(`daily output: ${dailyRoot}`);
  console.log(`monthly output: ${monthlyRoot}`);
  console.log(`cache: ${cacheRoot}`);
  console.log('');

  const stats = createStats(tasks.length);
  const results = await runPool(tasks, concurrency, (task) => downloadDailyTask(getHistoricalRates, task, stats));
  await flushProgressWrites();
  clearProgressLine();

  const failed = results.filter((result) => result.status === 'failed');
  const elapsedSeconds = ((Date.now() - startedAt.getTime()) / 1000).toFixed(1);

  console.log(`daily finished in ${elapsedSeconds}s`);
  console.log(`done: ${stats.done}, skipped: ${stats.skipped}, failed: ${stats.failed}`);

  if (failed.length > 0) {
    console.log('');
    console.log('failed daily tasks, rerun this script to continue:');
    for (const item of failed) {
      console.log(`- ${item.task.pair} ${item.task.day.label}: ${item.error}`);
    }
    process.exitCode = 1;
    return;
  }

  console.log('');
  console.log('aggregating daily files into monthly files...');
  const aggregateResults = await aggregateMonthlyFiles(pairs, months);
  await flushProgressWrites();

  const aggregateFailed = aggregateResults.filter((result) => result.status === 'failed');
  const aggregateDone = aggregateResults.filter((result) => result.status === 'done').length;
  const aggregateSkipped = aggregateResults.filter((result) => result.status === 'skipped').length;

  console.log(`monthly aggregation done: ${aggregateDone}, skipped: ${aggregateSkipped}, failed: ${aggregateFailed.length}`);
  if (aggregateFailed.length > 0) {
    console.log('');
    console.log('failed monthly aggregates:');
    for (const item of aggregateFailed) {
      console.log(`- ${item.pair} ${item.month.label}: ${item.error}`);
    }
    process.exitCode = 1;
  }
}

function requireDukascopyNode() {
  if (!fs.existsSync(DUKASCOPY_NODE_PATH)) {
    throw new Error(`dukascopy-node was not found at ${DUKASCOPY_NODE_PATH}`);
  }

  const pkg = require(DUKASCOPY_NODE_PATH);
  if (typeof pkg.getHistoricalRates !== 'function') {
    throw new Error(`dukascopy-node at ${DUKASCOPY_NODE_PATH} does not export getHistoricalRates`);
  }

  return pkg;
}

function buildDailyTasks(pairList, days) {
  const tasks = [];
  for (const pair of pairList) {
    for (const day of days) {
      const pairDir = path.join(dailyRoot, pair, day.monthLabel);
      const finalPath = path.join(pairDir, `${pair}_${timeframe}_${day.label}.csv`);
      tasks.push({
        id: `${pair}:${day.label}`,
        pair,
        day,
        pairDir,
        finalPath,
        partPath: `${finalPath}.part`
      });
    }
  }
  return tasks;
}

async function downloadDailyTask(getHistoricalRates, task, stats) {
  try {
    if (await isNonEmptyFile(task.finalPath)) {
      await updateTaskProgress(task, 'done', {
        skipped: true,
        output: task.finalPath,
        finishedAt: new Date().toISOString()
      });
      stats.skipped += 1;
      stats.finished += 1;
      renderProgress(stats);
      return { status: 'skipped', task };
    }

    await fsp.mkdir(task.pairDir, { recursive: true });
    await updateTaskProgress(task, 'running', {
      skipped: false,
      output: task.finalPath,
      startedAt: new Date().toISOString()
    });

    const csv = await getHistoricalRates({
      instrument: task.pair,
      dates: {
        from: task.day.from,
        to: task.day.to
      },
      timeframe,
      format: 'csv',
      priceType: 'bid',
      volumes: true,
      useCache: true,
      cacheFolderPath: cacheRoot,
      batchSize: 5,
      pauseBetweenBatchesMs: 1500,
      retryCount: 5,
      pauseBetweenRetriesMs: 5000,
      retryOnEmpty: false
    });

    const csvText = normalizeCsv(csv);
    if (!csvText.trim()) {
      if (!isExpectedClosedDay(task.day.from)) {
        throw new Error('dukascopy-node returned an empty CSV');
      }

      await fsp.writeFile(task.partPath, getEmptyCsv(), 'utf8');
      await fsp.rename(task.partPath, task.finalPath);
      await updateTaskProgress(task, 'done', {
        skipped: false,
        closed: true,
        output: task.finalPath,
        bytes: Buffer.byteLength(getEmptyCsv(), 'utf8'),
        finishedAt: new Date().toISOString()
      });

      stats.done += 1;
      stats.finished += 1;
      renderProgress(stats);
      return { status: 'done', task };
    }

    await fsp.writeFile(task.partPath, csvText, 'utf8');
    await fsp.rename(task.partPath, task.finalPath);
    await updateTaskProgress(task, 'done', {
      skipped: false,
      output: task.finalPath,
      bytes: Buffer.byteLength(csvText, 'utf8'),
      finishedAt: new Date().toISOString()
    });

    stats.done += 1;
    stats.finished += 1;
    renderProgress(stats);
    return { status: 'done', task };
  } catch (error) {
    await updateTaskProgress(task, 'failed', {
      output: task.finalPath,
      error: formatError(error),
      failedAt: new Date().toISOString()
    });
    stats.failed += 1;
    stats.finished += 1;
    renderProgress(stats);
    return { status: 'failed', task, error: formatError(error) };
  }
}

async function aggregateMonthlyFiles(pairList, months) {
  const results = [];

  for (const pair of pairList) {
    const pairMonthlyDir = path.join(monthlyRoot, pair);
    await fsp.mkdir(pairMonthlyDir, { recursive: true });

    for (const month of months) {
      const finalPath = path.join(pairMonthlyDir, `${pair}_${timeframe}_${month.label}.csv`);
      const partPath = `${finalPath}.part`;
      const monthlyId = `${pair}:${month.label}`;

      try {
        const days = getDaysInMonth(month);
        const inputPaths = days.map((day) => path.join(dailyRoot, pair, month.label, `${pair}_${timeframe}_${day.label}.csv`));
        const missing = [];

        for (const inputPath of inputPaths) {
          if (!(await isNonEmptyFile(inputPath))) {
            missing.push(inputPath);
          }
        }

        if (missing.length > 0) {
          throw new Error(`missing ${missing.length} daily files`);
        }

        if (await monthlyIsFresh(finalPath, inputPaths)) {
          await updateMonthlyProgress(monthlyId, pair, month, 'done', {
            skipped: true,
            output: finalPath,
            finishedAt: new Date().toISOString()
          });
          console.log(`[skip monthly] ${pair} ${month.label}`);
          results.push({ status: 'skipped', pair, month });
          continue;
        }

        await writeMonthlyFile(inputPaths, partPath);
        await fsp.rename(partPath, finalPath);
        const stat = await fsp.stat(finalPath);
        await updateMonthlyProgress(monthlyId, pair, month, 'done', {
          skipped: false,
          output: finalPath,
          bytes: stat.size,
          finishedAt: new Date().toISOString()
        });
        console.log(`[monthly] ${pair} ${month.label}`);
        results.push({ status: 'done', pair, month });
      } catch (error) {
        await updateMonthlyProgress(monthlyId, pair, month, 'failed', {
          output: finalPath,
          error: formatError(error),
          failedAt: new Date().toISOString()
        });
        console.log(`[fail monthly] ${pair} ${month.label}: ${formatError(error)}`);
        results.push({ status: 'failed', pair, month, error: formatError(error) });
      }
    }
  }

  return results;
}

async function writeMonthlyFile(inputPaths, outputPath) {
  await fsp.rm(outputPath, { force: true });
  let wroteHeader = false;

  for (const inputPath of inputPaths) {
    const content = await fsp.readFile(inputPath, 'utf8');
    const normalized = content.endsWith('\n') ? content : `${content}\n`;
    const lines = normalized.split(/\r?\n/);

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      if (!line) {
        continue;
      }
      if (i === 0 && isHeaderLine(line)) {
        if (wroteHeader) {
          continue;
        }
        wroteHeader = true;
      }
      await fsp.appendFile(outputPath, `${line}\n`, 'utf8');
    }
  }
}

function isHeaderLine(line) {
  return /^timestamp[,;]/i.test(line.trim());
}

function getEmptyCsv() {
  if (timeframe === 'tick') {
    return 'timestamp,askPrice,bidPrice,askVolume,bidVolume\n';
  }
  return 'timestamp,open,high,low,close,volume\n';
}

function isExpectedClosedDay(date) {
  const day = date.getUTCDay();
  return day === 0 || day === 6;
}

async function monthlyIsFresh(monthlyPath, inputPaths) {
  try {
    const monthlyStat = await fsp.stat(monthlyPath);
    if (!monthlyStat.isFile() || monthlyStat.size === 0) {
      return false;
    }

    for (const inputPath of inputPaths) {
      const inputStat = await fsp.stat(inputPath);
      if (inputStat.mtimeMs > monthlyStat.mtimeMs) {
        return false;
      }
    }
    return true;
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return false;
    }
    throw error;
  }
}

async function runPool(tasks, limit, worker) {
  const results = new Array(tasks.length);
  let nextIndex = 0;

  renderProgress(createStats(tasks.length));
  const workers = Array.from({ length: Math.min(limit, tasks.length) }, async () => {
    while (nextIndex < tasks.length) {
      const currentIndex = nextIndex;
      nextIndex += 1;
      results[currentIndex] = await worker(tasks[currentIndex]);
    }
  });

  await Promise.all(workers);
  return results;
}

function createStats(total) {
  return {
    total,
    finished: 0,
    done: 0,
    skipped: 0,
    failed: 0,
    startedAt: Date.now()
  };
}

function renderProgress(stats) {
  const width = Math.max(10, Math.min(40, (process.stdout.columns || 80) - 48));
  const ratio = stats.total === 0 ? 1 : stats.finished / stats.total;
  const filled = Math.round(width * ratio);
  const bar = `${'#'.repeat(filled)}${'.'.repeat(width - filled)}`;
  const percent = String(Math.floor(ratio * 100)).padStart(3, ' ');
  const elapsed = ((Date.now() - stats.startedAt) / 1000).toFixed(0).padStart(4, ' ');
  const text = `[${bar}] ${percent}% ${stats.finished}/${stats.total} done=${stats.done} skip=${stats.skipped} fail=${stats.failed} ${elapsed}s`;
  process.stdout.write(`\r${text}`);
}

function clearProgressLine() {
  if (!process.stdout.isTTY) {
    process.stdout.write('\n');
    return;
  }
  process.stdout.write(`\r${' '.repeat(process.stdout.columns || 100)}\r`);
}

function getLastCompleteMonths(count) {
  const now = new Date();
  const firstDayOfCurrentMonth = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const months = [];

  for (let i = count; i >= 1; i -= 1) {
    const from = new Date(Date.UTC(
      firstDayOfCurrentMonth.getUTCFullYear(),
      firstDayOfCurrentMonth.getUTCMonth() - i,
      1
    ));
    const to = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth() + 1, 1));
    months.push({
      label: formatMonth(from),
      from,
      to
    });
  }

  return months;
}

function getMonthsInRange(fromValue, toValue) {
  if (!fromValue || !toValue) {
    throw new Error('both --from and --to are required when using a custom range');
  }

  const from = parseMonthStart(fromValue, '--from');
  const to = parseMonthStart(toValue, '--to');
  if (from >= to) {
    throw new Error(`--from must be earlier than --to, got ${fromValue} and ${toValue}`);
  }

  const months = [];
  for (
    let current = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), 1));
    current < to;
    current = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() + 1, 1))
  ) {
    const next = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() + 1, 1));
    months.push({
      label: formatMonth(current),
      from: current,
      to: next
    });
  }

  return months;
}

function getDaysFromMonths(months) {
  return months.flatMap((month) => getDaysInMonth(month));
}

function getDaysInMonth(month) {
  const days = [];
  for (
    let current = new Date(Date.UTC(month.from.getUTCFullYear(), month.from.getUTCMonth(), month.from.getUTCDate()));
    current < month.to;
    current = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), current.getUTCDate() + 1))
  ) {
    const next = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), current.getUTCDate() + 1));
    days.push({
      label: formatDay(current),
      monthLabel: month.label,
      from: current,
      to: next
    });
  }
  return days;
}

function parseMonthStart(value, flagName) {
  const match = /^(\d{4})-(\d{2})(?:-\d{2})?$/.exec(value || '');
  if (!match) {
    throw new Error(`${flagName} must be YYYY-MM or YYYY-MM-DD, got ${value}`);
  }

  const year = Number(match[1]);
  const month = Number(match[2]);
  if (month < 1 || month > 12) {
    throw new Error(`${flagName} has an invalid month: ${value}`);
  }

  return new Date(Date.UTC(year, month - 1, 1));
}

function formatMonth(date) {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`;
}

function formatDay(date) {
  return `${formatMonth(date)}-${String(date.getUTCDate()).padStart(2, '0')}`;
}

function normalizeCsv(value) {
  if (typeof value === 'string') {
    return value.endsWith('\n') ? value : `${value}\n`;
  }

  if (Array.isArray(value)) {
    return `${value.map((row) => row.join(',')).join('\n')}\n`;
  }

  throw new Error(`unexpected CSV output type: ${typeof value}`);
}

async function isNonEmptyFile(filePath) {
  try {
    const stat = await fsp.stat(filePath);
    return stat.isFile() && stat.size > 0;
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return false;
    }
    throw error;
  }
}

async function loadProgress() {
  try {
    const raw = await fsp.readFile(progressPath, 'utf8');
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      throw new Error('progress file is not an object');
    }
    parsed.tasks = parsed.tasks && typeof parsed.tasks === 'object' ? parsed.tasks : {};
    parsed.monthly = parsed.monthly && typeof parsed.monthly === 'object' ? parsed.monthly : {};
    return parsed;
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return {
        updatedAt: null,
        settings: {},
        tasks: {},
        monthly: {}
      };
    }
    throw error;
  }
}

async function updateTaskProgress(task, status, patch) {
  const previous = progress.tasks[task.id] || {};
  progress.tasks[task.id] = {
    ...previous,
    id: task.id,
    pair: task.pair,
    day: task.day.label,
    month: task.day.monthLabel,
    status,
    attempts: status === 'running' ? (previous.attempts || 0) + 1 : (previous.attempts || 0),
    updatedAt: new Date().toISOString(),
    ...patch
  };

  progress.updatedAt = new Date().toISOString();
  await saveProgress();
}

async function updateMonthlyProgress(id, pair, month, status, patch) {
  const previous = progress.monthly[id] || {};
  progress.monthly[id] = {
    ...previous,
    id,
    pair,
    month: month.label,
    status,
    updatedAt: new Date().toISOString(),
    ...patch
  };

  progress.updatedAt = new Date().toISOString();
  await saveProgress();
}

function saveProgress() {
  progressWriteQueue = progressWriteQueue.then(async () => {
    const tmpPath = `${progressPath}.tmp`;
    const snapshot = JSON.stringify(progress, null, 2);
    await fsp.mkdir(path.dirname(progressPath), { recursive: true });
    await fsp.writeFile(tmpPath, `${snapshot}\n`, 'utf8');
    await fsp.rename(tmpPath, progressPath);
  });

  return progressWriteQueue;
}

async function flushProgressWrites() {
  await progressWriteQueue;
}

function parseArgs(argv) {
  const parsed = {};

  for (let i = 0; i < argv.length; i += 1) {
    const item = argv[i];
    if (!item.startsWith('--')) {
      continue;
    }

    const eqIndex = item.indexOf('=');
    if (eqIndex !== -1) {
      parsed[item.slice(2, eqIndex)] = item.slice(eqIndex + 1);
      continue;
    }

    const key = item.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith('--')) {
      parsed[key] = next;
      i += 1;
    } else {
      parsed[key] = 'true';
    }
  }

  return parsed;
}

function readPositiveInteger(value, fallback) {
  if (value === undefined) {
    return fallback;
  }

  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`expected a positive integer, got ${value}`);
  }
  return parsed;
}

function formatError(error) {
  if (!error) {
    return 'unknown error';
  }
  if (error.stack) {
    return error.stack.split('\n')[0];
  }
  return String(error.message || error);
}
