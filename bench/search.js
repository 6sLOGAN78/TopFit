// Spike S3: k6 load test for GET /api/jobs/search at a constant 20 rps.
//
//   docker run --rm --network host -v "$PWD/bench:/bench:ro" \
//     -e BASE_URL=http://127.0.0.1:8100 grafana/k6:<pinned> run /bench/search.js
//
// Thresholds are code: the run exits non-zero when p95 >= 300 ms or >= 1% of requests fail.
// Env: BASE_URL (default http://127.0.0.1:8100), RATE (20), DURATION (5m),
//      CACHE=cold|warm (default cold).
// cold appends a random letters-only word so the in-process query-embedding LRU never hits
// (worst case: every request pays a fastembed call). warm sends the queries verbatim, so after
// the first pass over the 200+ queries the embedding is cached (the repeat-query case).
import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';
import { Counter } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8100';
const RATE = parseInt(__ENV.RATE || '20', 10);
const DURATION = __ENV.DURATION || '5m';
const COLD = (__ENV.CACHE || 'cold') !== 'warm';

const queries = new SharedArray('queries', () =>
  open('./queries.txt')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && !l.startsWith('#')),
);

const degraded = new Counter('search_degraded_responses');
const emptyResults = new Counter('search_empty_results');

export const options = {
  scenarios: {
    search: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 40,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<300'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

function nonce() {
  const letters = 'abcdefghijklmnopqrstuvwxyz';
  let out = '';
  for (let i = 0; i < 8; i++) out += letters[Math.floor(Math.random() * letters.length)];
  return out;
}

export default function () {
  let q = queries[Math.floor(Math.random() * queries.length)];
  if (COLD) q = `${q} ${nonce()}`;
  const res = http.get(`${BASE_URL}/api/jobs/search?q=${encodeURIComponent(q)}`, {
    tags: { name: 'GET /api/jobs/search' },
  });
  let body = null;
  try {
    body = res.json();
  } catch (_) {
    body = null;
  }
  check(res, {
    'status is 200': (r) => r.status === 200,
    'has items array': () => body !== null && Array.isArray(body.items),
  });
  if (body && body.degraded === true) degraded.add(1);
  if (body && Array.isArray(body.items) && body.items.length === 0) emptyResults.add(1);
}
