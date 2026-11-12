#!/usr/bin/env node
// Read-only candle bridge for ict-entry-model-studio.
//   node bridge.mjs <EXCHANGE:SYMBOL> <timeframe> <count>
//   e.g. node bridge.mjs OANDA:NAS100USD 1 1000      (timeframe: 1 5 15 60 240 D)
// Prints a JSON array [{time (unix seconds, bar OPEN), open, high, low, close, volume}] oldest first.
// Uses ONLY getCandles from Mathieu2301/TradingView-API (anonymous, no login). Errors go to stderr, exit 1.
import { getCandles } from '@mathieuc/tradingview/data';

const [symbol, timeframe, countArg] = process.argv.slice(2);
const count = Number(countArg);
if (!symbol || !timeframe || !Number.isInteger(count) || count < 1) {
  process.stderr.write('usage: bridge.mjs <EXCHANGE:SYMBOL> <timeframe> <count>\n');
  process.exit(2);
}

try {
  const candles = await getCandles({ symbol, timeframe, count, timeoutMs: 30_000 });
  const rows = candles.map(({ time, open, high, low, close, volume }) => ({ time, open, high, low, close, volume }));
  await new Promise((resolve) => process.stdout.write(JSON.stringify(rows), resolve));
  process.exit(0);
} catch (e) {
  process.stderr.write(`${e?.code ?? 'ERROR'}: ${e?.message ?? e}\n`);
  process.exit(1);
}
