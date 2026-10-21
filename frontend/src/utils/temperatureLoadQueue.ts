type WaitingJob = { start: () => void };

export function createTemperatureLoadQueue(limit = 2) {
  if (!Number.isInteger(limit) || limit < 1) {
    throw new RangeError('Temperature load concurrency must be a positive integer.');
  }

  let active = 0;
  const waiting: WaitingJob[] = [];
  const abortError = () => new DOMException('Temperature load aborted.', 'AbortError');

  function drain() {
    while (active < limit && waiting.length > 0) {
      const job = waiting.shift()!;
      job.start();
    }
  }

  function run<T>(signal: AbortSignal, load: () => Promise<T>): Promise<T> {
    if (signal.aborted) return Promise.reject(abortError());

    return new Promise<T>((resolve, reject) => {
      const cancel = () => {
        const index = waiting.indexOf(job);
        if (index !== -1) waiting.splice(index, 1);
        reject(abortError());
      };
      const job: WaitingJob = {
        start: () => {
          if (signal.aborted) {
            signal.removeEventListener('abort', cancel);
            reject(abortError());
            return;
          }
          active += 1;
          void (async () => {
            try {
              const result = await load();
              if (signal.aborted) reject(abortError());
              else resolve(result);
            } catch (error) {
              reject(signal.aborted ? abortError() : error);
            } finally {
              signal.removeEventListener('abort', cancel);
              // Cancellation cannot free capacity while the underlying load is still running.
              active -= 1;
              drain();
            }
          })();
        },
      };
      signal.addEventListener('abort', cancel, { once: true });
      waiting.push(job);
      drain();
    });
  }

  return { run };
}

export type TemperatureLoadQueue = ReturnType<typeof createTemperatureLoadQueue>;
