// Collector preload: Node itself executes the target as the real entrypoint.
import { readFileSync, writeFileSync } from 'node:fs';
import { performance, monitorEventLoopDelay } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';

function installCollector() {
  const configPath = process.env.BOTTLENECK_LENS_CONFIG;
  const resultPath = process.env.BOTTLENECK_LENS_RESULT;
  // A fork/worker can inherit --import. It must not write the parent's metrics.
  delete process.env.BOTTLENECK_LENS_CONFIG;
  delete process.env.BOTTLENECK_LENS_RESULT;
  if (!configPath || !resultPath) return;
  const config = JSON.parse(readFileSync(configPath, 'utf8'));
  if (Number(process.versions.node.split('.')[0]) < 22) {
    throw new Error('The Node adapter requires Node.js 22+');
  }
  const external = process.env.NODE_OPTIONS ?? '';
  if (/--(?:cpu-prof|heap-prof|prof\b|inspect)/.test(external)) {
    throw new Error('Disable external Node profiling/inspection before collection');
  }
  if (config.mode === 'memory' || config.target.kind !== 'script') {
    throw new Error('The Node adapter supports script benchmark and CPU modes');
  }
  const runtime = { name: 'node', version: process.version, executable: process.execPath, v8: process.versions.v8 };
  if (config.expected_runtime && Object.entries(config.expected_runtime).some(([key, value]) => runtime[key] !== value)) {
    throw new Error('Recorded Node runtime changed; target was not executed');
  }
  // Keep our own preload out of default fork()/Worker execArgv, while retaining
  // user flags and profiler flags. Native entrypoint/require.main remain intact.
  const index = process.execArgv.findIndex((value, i, values) =>
    value === '--import' && values[i + 1] === fileURLToPath(import.meta.url));
  if (index !== -1) process.execArgv.splice(index, 2);

  const delay = config.mode === 'cpu' ? monitorEventLoopDelay({ resolution: 10 }) : null;
  delay?.enable();
  const loopStart = performance.eventLoopUtilization();
  const cpuStart = process.cpuUsage();
  const started = performance.now();

  // The target executes after this preload. Pending timers/I/O keep Node alive;
  // later application exit hooks are outside the measured interval.
  process.once('exit', (code) => {
    if (code !== 0) return;
    const wall = (performance.now() - started) / 1000;
    const cpu = process.cpuUsage(cpuStart);
    const metrics = { wall_seconds: wall, cpu_seconds: (cpu.user + cpu.system) / 1e6,
      process_peak_rss_bytes: process.resourceUsage().maxRSS * 1024 };
    const detail = {};
    if (delay) {
      delay.disable();
      const utilization = performance.eventLoopUtilization(loopStart);
      metrics.event_loop_utilization_ratio = utilization.utilization;
      detail.event_loop = { resolution_ms: 10, samples: delay.count,
        scope: 'Main event loop during preload-to-exit interval; diagnostics only, excludes workers and children.' };
      if (delay.count > 0) {
        metrics.event_loop_delay_mean_seconds = delay.mean / 1e9;
        metrics.event_loop_delay_p99_seconds = delay.percentile(99) / 1e9;
        metrics.event_loop_delay_max_seconds = delay.max / 1e9;
      }
    }
    writeFileSync(resultPath, JSON.stringify({ metrics, detail, runtime }), { flag: 'wx' });
  });
}

installCollector();
