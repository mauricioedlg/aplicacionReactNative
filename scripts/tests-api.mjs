// Run with Node 22+: node --test scripts/tests-api.mjs
// Exercise the real API module with isolated native/browser adapters. No network,
// device storage, database or extra test dependencies are used.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  fileName: 'api.ts',
}).outputText;

const reply = (body, status = 200) => ({
  status, ok: status >= 200 && status < 300, json: async () => body,
});
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

function loadApi({ platform = 'android', androidVersion = 36, url = 'https://api.example.test/', webUrl, androidUrl, sameOrigin, browserOrigin = 'https://app.example.test', dev = true, fetch: fetchImpl, secureStore: storeOverrides = {}, permissions: permissionOverrides = {} } = {}) {
  const requests = [];
  const storage = new Map();
  const storageCalls = [];
  const timers = new Map();
  const logs = [];
  const permissionCalls = [];
  const permissions = {
    PERMISSIONS: { ACCESS_LOCAL_NETWORK: 'android.permission.ACCESS_LOCAL_NETWORK' },
    RESULTS: { GRANTED: 'granted', DENIED: 'denied', NEVER_ASK_AGAIN: 'never_ask_again' },
    async check(permission) { permissionCalls.push(['check', permission]); return permissionOverrides.check ? permissionOverrides.check(permission) : true; },
    async request(permission, rationale) { permissionCalls.push(['request', permission]); return permissionOverrides.request ? permissionOverrides.request(permission, rationale) : 'granted'; },
  };
  let timerId = 0;
  let fetchHandler = fetchImpl || (async () => reply({ ok: true }));
  const secureStore = {
    async getItemAsync(key) { storageCalls.push(['get', key]); return storage.get(key) ?? null; },
    async setItemAsync(key, value) { storageCalls.push(['set', key, value]); storage.set(key, value); },
    async deleteItemAsync(key) { storageCalls.push(['delete', key]); storage.delete(key); },
    ...storeOverrides,
  };
  const forbiddenStorage = new Proxy({}, {
    get() { throw new Error('Session credentials must never access browser storage.'); },
  });
  const module = { exports: {} };
  const context = {
    module, exports: module.exports, process: { env: { EXPO_PUBLIC_API_URL: url, EXPO_PUBLIC_API_URL_WEB: webUrl, EXPO_PUBLIC_API_URL_ANDROID: androidUrl, EXPO_PUBLIC_API_SAME_ORIGIN: sameOrigin } },
    __DEV__: dev, AbortController, URL,
    console: { info: (...values) => logs.push(values.join(' ')), warn: (...values) => logs.push(values.join(' ')) },
    localStorage: forbiddenStorage, sessionStorage: forbiddenStorage,
    ...(browserOrigin === null ? {} : { window: { location: { origin: browserOrigin }, localStorage: forbiddenStorage, sessionStorage: forbiddenStorage } }),
    setTimeout(callback, ms) { const id = ++timerId; timers.set(id, { callback, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    async fetch(requestUrl, options) {
      requests.push({ url: requestUrl, ...options });
      return fetchHandler(requestUrl, options);
    },
    require(name) {
      if (name === 'react-native') return { Platform: { OS: platform, Version: androidVersion }, PermissionsAndroid: permissions };
      if (name === 'expo-secure-store') return secureStore;
      throw new Error(`Unexpected dependency: ${name}`);
    },
  };
  vm.runInNewContext(compiled, context, { filename: 'src/services/api.ts' });
  return {
    api: module.exports, requests, storage, storageCalls, timers, logs, permissionCalls,
    setFetch(handler) { fetchHandler = handler; },
  };
}

test('web and Android can reach a local backend independently of the phone LAN address', async () => {
  for (const [platform, expected] of [
    ['web', 'http://localhost:8000'],
    ['android', 'http://10.0.2.2:8000'],
    ['ios', 'http://192.168.16.168:8000'],
  ]) {
    const { api, requests } = loadApi({ platform, url: 'http://192.168.16.168:8000', webUrl: ' http://localhost:8000/ ', androidUrl: ' http://10.0.2.2:8000/ ' });
    await api.peticionSistema('csrf/');
    assert.equal(api.API_URL, expected);
    assert.equal(requests[0].url, `${expected}/api/sistema/app/csrf/`);
  }
});

test('same-origin web builds follow their public domain and keep cookie and CSRF authentication', async () => {
  for (const browserOrigin of ['https://gestion-proyectos.onrender.com', 'https://app.example.test']) {
    const { api, requests, storageCalls } = loadApi({
      platform: 'web', sameOrigin: 'true', browserOrigin, dev: false,
      url: 'https://previous-backend.example.test', webUrl: 'http://localhost:8000',
      fetch: async requestUrl => requestUrl.endsWith('/csrf/')
        ? reply({ ok: true, csrfToken: 'csrf-for-current-domain' })
        : reply({ ok: true }),
    });
    await api.peticionSistema('login/', { metodo: 'POST', datos: { usuario: 'test' } });
    assert.equal(api.API_URL, browserOrigin);
    assert.deepEqual(requests.map(request => request.url), [
      `${browserOrigin}/api/sistema/app/csrf/`,
      `${browserOrigin}/api/sistema/app/login/`,
    ]);
    assert.equal(requests[1].headers['X-CSRFToken'], 'csrf-for-current-domain');
    assert.equal(requests[1].headers.Authorization, undefined);
    assert.ok(requests.every(request => request.credentials === 'include'));
    assert.equal(storageCalls.length, 0);
  }
});

test('same-origin web imports safely without a browser and never falls back to another API during SSR', async () => {
  const { api, requests } = loadApi({ platform: 'web', sameOrigin: 'true', browserOrigin: null, dev: false });
  assert.equal(api.API_URL, '');
  await assert.rejects(api.peticionSistema('yo/'), error => error instanceof api.ApiError && error.status === 0);
  assert.equal(requests.length, 0);
});

test('same-origin flag leaves native URLs and explicit separate-backend web builds unchanged', async () => {
  for (const [platform, expected] of [
    ['android', 'https://android-api.example.test'],
    ['ios', 'https://api.example.test'],
  ]) {
    const { api, requests } = loadApi({ platform, sameOrigin: 'true', androidUrl: 'https://android-api.example.test', browserOrigin: null });
    await api.peticionSistema('yo/');
    assert.equal(api.API_URL, expected);
    assert.equal(requests[0].url, `${expected}/api/sistema/app/yo/`);
  }
  for (const sameOrigin of [undefined, 'false', 'TRUE']) {
    const { api } = loadApi({ platform: 'web', sameOrigin, webUrl: 'https://separate-api.example.test' });
    assert.equal(api.API_URL, 'https://separate-api.example.test');
  }
});

test('same-origin production builds still require HTTPS', async () => {
  const { api, requests } = loadApi({ platform: 'web', sameOrigin: 'true', browserOrigin: 'http://localhost:3000', dev: false });
  await assert.rejects(api.peticionSistema('csrf/'), error => error instanceof api.ApiError && /HTTPS/.test(error.message));
  assert.equal(requests.length, 0);
});

test('Android 17 waits for local network permission before sending a login', async () => {
  const grant = deferred();
  const prompted = deferred();
  const { api, requests, timers, permissionCalls } = loadApi({
    androidVersion: 37, url: 'http://10.0.2.2:8000',
    permissions: { check: async () => false, request: () => { prompted.resolve(); return grant.promise; } },
  });
  const pending = api.peticionSistema('movil/login/', { metodo: 'POST', datos: { usuario: 'test' } });
  await prompted.promise;
  assert.equal(requests.length, 0);
  assert.equal(timers.size, 0); // El diálogo no consume el plazo HTTP.
  grant.resolve('granted');
  await pending;
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, 'POST');
  assert.deepEqual(permissionCalls.map(call => call[0]), ['check', 'request']);
});

test('denying local network access explains Android permissions and sends no HTTP', async () => {
  for (const decision of ['denied', 'never_ask_again']) {
    const { api, requests, timers } = loadApi({
      androidVersion: 37, url: 'http://192.168.1.71:8000',
      permissions: { check: async () => false, request: async () => decision },
    });
    await assert.rejects(api.peticionSistema('movil/login/', { metodo: 'POST', datos: {} }),
      error => error instanceof api.ApiError && /red local.*Ajustes de Android/.test(error.message));
    assert.equal(requests.length, 0);
    assert.equal(timers.size, 0);
  }
});

test('concurrent requests share one local network dialog and recheck after revocation', async () => {
  const grant = deferred();
  const prompted = deferred();
  let allowed = false;
  let decision = grant.promise;
  const { api, permissionCalls, requests } = loadApi({
    androidVersion: 37, url: 'http://10.0.2.2:8000',
    permissions: { check: async () => allowed, request: () => { prompted.resolve(); return decision; } },
  });
  const first = api.peticionSistema('yo/');
  const second = api.peticionSistema('csrf/');
  await prompted.promise;
  assert.equal(permissionCalls.filter(call => call[0] === 'request').length, 1);
  grant.resolve('granted');
  await Promise.all([first, second]);
  allowed = true;
  await api.peticionSistema('yo/');
  assert.equal(permissionCalls.filter(call => call[0] === 'request').length, 1);
  allowed = false;
  decision = Promise.resolve('denied');
  await assert.rejects(api.peticionSistema('yo/'), /red local/);
  assert.equal(permissionCalls.filter(call => call[0] === 'request').length, 2);
  assert.equal(requests.length, 3);
});

test('a caller can cancel while another request keeps waiting for the shared permission', async () => {
  const grant = deferred();
  const prompted = deferred();
  const controller = new AbortController();
  const { api, requests, permissionCalls } = loadApi({
    androidVersion: 37, url: 'http://10.0.2.2:8000',
    permissions: { check: async () => false, request: () => { prompted.resolve(); return grant.promise; } },
  });
  const first = api.peticionSistema('yo/', { signal: controller.signal });
  const second = api.peticionSistema('csrf/');
  await prompted.promise;
  const cancelled = new Error('caller cancelled');
  controller.abort(cancelled);
  await assert.rejects(first, error => error === cancelled);
  assert.equal(requests.length, 0);
  grant.resolve('granted');
  await second;
  assert.equal(requests.length, 1);
  assert.equal(permissionCalls.filter(call => call[0] === 'request').length, 1);
});

test('Android 17 reports a malformed local backend URL without asking for permissions', async () => {
  const { api, requests, permissionCalls } = loadApi({ androidVersion: 37, url: 'http://[' });
  await assert.rejects(api.peticionSistema('yo/'), error => error instanceof api.ApiError && /URL válida/.test(error.message));
  assert.equal(requests.length, 0);
  assert.equal(permissionCalls.length, 0);
});

test('local permission covers LAN hosts without prompting other platforms or public and loopback backends', async () => {
  for (const url of ['http://10.0.2.2:8000', 'http://172.16.0.1', 'http://172.31.255.254', 'http://192.168.1.71', 'http://169.254.1.1', 'http://[fd00::1]', 'http://[fe80::1]', 'http://servidor.local', 'http://servidor']) {
    const { api, permissionCalls } = loadApi({ androidVersion: 37, url });
    await api.peticionSistema('yo/');
    assert.equal(permissionCalls.length, 1, url);
    assert.equal(permissionCalls[0][0], 'check', url);
  }
  for (const options of [
    { androidVersion: 36, url: 'http://10.0.2.2:8000' },
    { platform: 'ios', url: 'http://10.0.2.2:8000' },
    { platform: 'web', url: 'http://192.168.1.71' },
    { androidVersion: 37, url: 'https://api.example.test' },
    { androidVersion: 37, url: 'http://172.15.1.1' },
    { androidVersion: 37, url: 'http://172.32.1.1' },
    { androidVersion: 37, url: 'http://192.168.1.71.example.test' },
    { androidVersion: 37, url: 'http://127.0.0.1:8000' },
    { androidVersion: 37, url: 'http://localhost:8000' },
    { androidVersion: 37, url: 'http://[::1]:8000' },
    { androidVersion: 37, dev: false, url: 'https://192.168.1.71' },
  ]) {
    const { api, permissionCalls } = loadApi(options);
    await api.peticionSistema('csrf/');
    assert.equal(permissionCalls.length, 0, JSON.stringify(options));
  }
});

test('empty platform URLs fall back to the shared backend; production still requires HTTPS', async () => {
  for (const platform of ['web', 'android']) {
    const { api } = loadApi({ platform, webUrl: ' ', androidUrl: ' ' });
    assert.equal(api.API_URL, 'https://api.example.test');
  }
  const { api, requests } = loadApi({ platform: 'web', dev: false, webUrl: 'http://localhost:8000' });
  await assert.rejects(api.peticionSistema('csrf/'), error => error instanceof api.ApiError && /HTTPS/.test(error.message));
  assert.equal(requests.length, 0);
});

test('development auth traces identify the request without exposing credentials or session tokens', async () => {
  const { api, logs } = loadApi({ fetch: async () => reply({ ok: true, token: 'private-session-value' }) });
  await api.guardarToken('previous-session-value');
  await api.peticionSistema('movil/registro/', { metodo: 'POST', datos: { usuario: 'private-user', contrasena: 'private-password', codigo: '987654', verificacion_id: 'private-challenge' } });
  const output = logs.join('\n');
  assert.match(output, /\[API android\] POST movil\/registro\/: inicio/);
  assert.match(output, /HTTP 200 \(\d+ ms\)/);
  assert.doesNotMatch(output, /private-|previous-session|987654|Bearer|Authorization/);
  const production = loadApi({ dev: false });
  await production.api.peticionSistema('yo/');
  assert.equal(production.logs.length, 0);
});

test('native login uses mobile endpoints and JSON without cookies or a stored session', async () => {
  const challenge = { ok: true, token: 'new-session-token', usuario: { id: 1, estado_acceso: 'sin_organizacion' } };
  const { api, requests, storageCalls } = loadApi({ fetch: async () => reply(challenge) });
  assert.equal(api.rutaAuth('login'), 'movil/login/');
  assert.equal(api.rutaAuth('registro'), 'movil/registro/');
  assert.deepEqual(await api.peticionSistema(api.rutaAuth('login'), {
    metodo: 'POST', datos: { usuario: 'admin', contrasena: 'test-password' },
  }), challenge);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'https://api.example.test/api/sistema/app/movil/login/');
  assert.equal(requests[0].credentials, 'omit');
  assert.equal(requests[0].headers['Content-Type'], 'application/json');
  assert.equal(requests[0].headers.Authorization, undefined);
  assert.equal(requests[0].headers['X-CSRFToken'], undefined);
  assert.deepEqual(JSON.parse(requests[0].body), { usuario: 'admin', contrasena: 'test-password' });
  assert.equal(storageCalls.length, 0);
});

test('native sessions use SecureStore and explicit Bearer while omitting cookies', async () => {
  const { api, requests, storage, storageCalls } = loadApi();
  assert.equal(api.hayToken(), false);
  await api.guardarToken('native-secret-token');
  assert.equal(storage.get('gestion.movil.sesion'), 'native-secret-token');
  await api.restaurarToken();
  await api.peticionSistema('finanzas/gastos-operativos/', { metodo: 'POST', datos: { concepto: 'Hosting', monto_mensual: '100.00' } });
  assert.equal(api.hayToken(), true);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].headers.Authorization, 'Bearer native-secret-token');
  assert.equal(requests[0].headers['X-CSRFToken'], undefined);
  assert.equal(requests[0].credentials, 'omit');
  assert.equal(requests[0].cache, 'no-store');
  assert.deepEqual(storageCalls.map(call => call[0]), ['set', 'get']);
});

test('web refreshes CSRF after login then caches it, without browser storage', async () => {
  const { api, requests, storageCalls } = loadApi({
    platform: 'web',
    fetch: async url => reply(url.endsWith('/csrf/') ? { ok: true, csrfToken: 'csrf-value' } : { ok: true }),
  });
  await api.guardarToken('must-not-be-written-to-browser-storage');
  await api.restaurarToken();
  assert.equal(api.rutaAuth('login'), 'login/');
  assert.equal(api.rutaAuth('registro'), 'registro/');
  await api.peticionSistema('login/', { metodo: 'POST', datos: { usuario: 'admin' } });
  await api.peticionSistema('organizacion/solicitudes/1/', { metodo: 'POST', datos: { decision: 'aprobar' } });
  await api.peticionSistema('clientes/', { metodo: 'POST', datos: {} });
  assert.equal(requests.length, 5);
  assert.ok(requests[0].url.endsWith('/csrf/'));
  assert.equal(requests[0].method, 'GET');
  for (const request of requests) {
    assert.equal(request.credentials, 'include');
    assert.equal(request.headers.Authorization, undefined);
  }
  assert.equal(requests[1].headers['X-CSRFToken'], 'csrf-value');
  assert.equal(requests[2].method, 'GET');
  assert.equal(requests[3].headers['X-CSRFToken'], 'csrf-value');
  assert.equal(requests[4].headers['X-CSRFToken'], 'csrf-value');
  assert.equal(storageCalls.length, 0);
});

test('web registration and logout invalidate the cached CSRF token', async () => {
  const { api, requests } = loadApi({
    platform: 'web',
    fetch: async url => reply(url.endsWith('/csrf/') ? { ok: true, csrfToken: 'csrf-value' } : { ok: true }),
  });
  await api.peticionSistema('registro/', { metodo: 'POST', datos: { codigo: '123456' } });
  await api.peticionSistema('salir/', { metodo: 'POST' });
  await api.peticionSistema('login/', { metodo: 'POST', datos: { usuario: 'admin' } });
  assert.equal(requests.filter(request => request.url.endsWith('/csrf/')).length, 3);
});

test('a current-session 401 triggers expiry, while authentication failures preserve the session', async () => {
  const { api } = loadApi({ fetch: async () => reply({ ok: false, mensaje: 'Sesión inválida.' }, 401) });
  await api.guardarToken('current-token');
  let expired = 0;
  const stop = api.escucharSesionExpirada(() => { expired++; });
  for (const route of ['login/', 'registro/', 'movil/login/', 'movil/registro/']) {
    await assert.rejects(api.peticionSistema(route), error => error instanceof api.ApiError && error.status === 401);
  }
  assert.equal(expired, 0);
  await assert.rejects(api.peticionSistema('yo/'), error => error instanceof api.ApiError && error.status === 401);
  assert.equal(expired, 1);
  stop();
  await assert.rejects(api.peticionSistema('yo/'));
  assert.equal(expired, 1);
});

test('a late 401 for an old token cannot expire a newer native session', async () => {
  const pending = deferred();
  const { api, requests, setFetch, storage } = loadApi({ fetch: () => pending.promise });
  await api.guardarToken('old-token');
  let expired = 0;
  api.escucharSesionExpirada(() => { expired++; void api.guardarToken(null); });
  const oldRequest = api.peticionSistema('dashboard-crm/');
  await api.guardarToken('new-token');
  pending.resolve(reply({ ok: false, mensaje: 'Sesión anterior revocada.' }, 401));
  await assert.rejects(oldRequest, error => error instanceof api.ApiError && error.status === 401);
  assert.equal(expired, 0);
  assert.equal(storage.get('gestion.movil.sesion'), 'new-token');
  setFetch(async () => reply({ ok: true, usuario: { id: 1 } }));
  await api.peticionSistema('yo/');
  assert.equal(requests[0].headers.Authorization, 'Bearer old-token');
  assert.equal(requests[1].headers.Authorization, 'Bearer new-token');
});

test('an unauthorized response expires the current session even when its body is not JSON', async () => {
  const { api } = loadApi({ fetch: async () => ({ status: 401, ok: false, json: async () => { throw new SyntaxError('html'); } }) });
  await api.guardarToken('current-token');
  let expired = 0;
  api.escucharSesionExpirada(() => { expired++; });
  await assert.rejects(api.peticionSistema('yo/'), error => error instanceof api.ApiError && error.status === 401);
  assert.equal(expired, 1);
});

test('permission errors preserve sessions and expose the server message', async () => {
  const { api } = loadApi({ fetch: async () => reply({ ok: false, mensaje: 'Solo administradores.' }, 403) });
  await api.guardarToken('collaborator-token');
  let expired = 0;
  let changed = 0;
  api.escucharSesionExpirada(() => { expired++; });
  const stopAccess = api.escucharAccesoActualizado(() => { changed++; });
  await assert.rejects(api.peticionSistema('finanzas/dashboard/'), error => error.status === 403 && error.message === 'Solo administradores.');
  assert.equal(expired, 0);
  assert.equal(changed, 1);
  assert.equal(api.hayToken(), true);
  stopAccess();
  await assert.rejects(api.peticionSistema('finanzas/dashboard/'));
  assert.equal(changed, 1);
});

test('the mobile app never restores the original business token', async () => {
  const { api, storage, requests } = loadApi();
  storage.set('meg.sistema.sesion', 'legacy-secret');
  await api.restaurarToken();
  assert.equal(api.hayToken(), false);
  await api.peticionSistema('yo/');
  assert.equal(requests[0].headers.Authorization, undefined);
  assert.equal(requests[0].url, 'https://api.example.test/api/sistema/app/yo/');
});

test('native registration sends account data only to the isolated application API', async () => {
  const { api, requests } = loadApi();
  await api.peticionSistema(api.rutaAuth('registro'), { metodo: 'POST', datos: { usuario: 'ana', correo: 'ana@example.test' } });
  assert.equal(requests[0].url, 'https://api.example.test/api/sistema/app/movil/registro/');
  assert.equal(requests[0].credentials, 'omit');
});

test('transport failures become readable API errors and clean their timeout', async () => {
  const { api, timers } = loadApi({ fetch: async () => { throw new TypeError('network down'); } });
  await assert.rejects(api.peticionSistema('yo/'), error => error instanceof api.ApiError && error.status === 0 && /conectar/.test(error.message));
  assert.equal(timers.size, 0);
});

test('requests time out after 20 seconds without waiting in the test', async () => {
  const { api, timers } = loadApi({
    fetch: (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
    }),
  });
  const pending = api.peticionSistema('yo/');
  assert.equal(timers.size, 1);
  const timeout = [...timers.values()][0];
  assert.equal(timeout.ms, 20000);
  timeout.callback();
  await assert.rejects(pending, error => error instanceof api.ApiError && error.status === 0 && /tardó demasiado/.test(error.message));
  assert.equal(timers.size, 0);
});

test('login and registration allow 60 seconds for remote authentication without retrying POSTs', async () => {
  for (const route of ['login/', 'registro/', 'movil/login/', 'movil/registro/']) {
    const pendingResponse = deferred();
    const { api, timers, requests } = loadApi({ fetch: () => pendingResponse.promise });
    const pending = api.peticionSistema(route, { metodo: 'POST', datos: {} });
    assert.equal([...timers.values()][0].ms, 60000, route);
    pendingResponse.resolve(reply({ ok: true }));
    await pending;
    assert.equal(requests.length, 1);
    assert.equal(timers.size, 0);
  }
});

test('authentication still aborts at its limit and caller cancellation remains immediate', async () => {
  for (const callerCancels of [false, true]) {
    const controller = new AbortController();
    const aborted = new Error('aborted');
    const { api, timers, requests } = loadApi({
      fetch: (_url, { signal }) => new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(aborted), { once: true });
      }),
    });
    const pending = api.peticionSistema('movil/login/', { metodo: 'POST', datos: {}, signal: controller.signal });
    if (callerCancels) {
      controller.abort();
      await assert.rejects(pending, error => error === aborted);
    } else {
      [...timers.values()][0].callback();
      await assert.rejects(pending, error => error.status === 0 && /tardó demasiado/.test(error.message));
    }
    assert.equal(requests.length, 1);
    assert.equal(timers.size, 0);
  }
});

test('caller cancellation stays distinguishable from network and timeout errors', async () => {
  const aborted = new Error('screen unmounted');
  const controller = new AbortController();
  const { api, timers } = loadApi({
    fetch: (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(aborted), { once: true });
    }),
  });
  const pending = api.peticionSistema('yo/', { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, error => error === aborted);
  assert.equal(timers.size, 0);
});

test('missing or invalid API configuration never sends a request', async () => {
  for (const options of [{ url: '' }, { url: 'postgresql://private' }, { url: 'http://api.example.test', dev: false }]) {
    const { api, requests } = loadApi(options);
    await assert.rejects(api.peticionSistema('yo/'), error => error instanceof api.ApiError && error.status === 0);
    assert.equal(requests.length, 0);
  }
});

test('non-JSON server failures report a useful configuration error', async () => {
  const { api } = loadApi({ fetch: async () => ({ status: 502, ok: false, json: async () => { throw new SyntaxError('html'); } }) });
  await assert.rejects(api.peticionSistema('yo/'), error => error.status === 502 && /JSON/.test(error.message));
});

test('an application-level rejection is not mistaken for a successful save', async () => {
  const { api } = loadApi({ fetch: async () => reply({ ok: false, mensaje: 'No se pudo guardar.' }) });
  await assert.rejects(api.peticionSistema('clientes/', { metodo: 'POST', datos: {} }),
    error => error instanceof api.ApiError && error.message === 'No se pudo guardar.');
});

test('a slow deletion is ordered before saving the next native session', async () => {
  const deletion = deferred();
  let onDelete;
  const started = new Promise(resolve => { onDelete = resolve; });
  const calls = [];
  let persisted = null;
  const { api } = loadApi({ secureStore: {
    async setItemAsync(_key, value) { calls.push(['set', value]); persisted = value; },
    async deleteItemAsync() { calls.push(['delete']); onDelete(); await deletion.promise; persisted = null; },
  } });
  await api.guardarToken('old-token');
  const remove = api.guardarToken(null);
  const replace = api.guardarToken('new-token');
  await started;
  assert.equal(api.hayToken(), true);
  assert.deepEqual(calls, [['set', 'old-token'], ['delete']]);
  deletion.resolve();
  await Promise.all([remove, replace]);
  assert.deepEqual(calls, [['set', 'old-token'], ['delete'], ['set', 'new-token']]);
  assert.equal(persisted, 'new-token');
});

test('SecureStore write failures restore the prior in-memory token', async () => {
  let fail = false;
  const { api, requests } = loadApi({ secureStore: {
    async setItemAsync() { if (fail) throw new Error('secure storage unavailable'); },
  } });
  await api.guardarToken('previous-token');
  fail = true;
  await assert.rejects(api.guardarToken('failed-token'), /secure storage unavailable/);
  await api.peticionSistema('yo/');
  assert.equal(requests[0].headers.Authorization, 'Bearer previous-token');
});
