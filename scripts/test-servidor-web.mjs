import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const { createWebApp } = require('../server.cjs');

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return `http://127.0.0.1:${server.address().port}`;
}

async function close(server) {
  if (!server.listening) return;
  const closed = once(server, 'close');
  server.close();
  server.closeAllConnections();
  await closed;
}

async function fixture(t, { trustRenderProxy = false } = {}) {
  const received = [];
  const backend = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    received.push({ url: req.url, method: req.method, headers: req.headers, body });
    res.writeHead(req.url === '/api/fallo/' ? 401 : 200, {
      'Content-Type': 'application/json',
      'Set-Cookie': [
        'movil_sesion=session-test; Path=/api/sistema/app/; HttpOnly; Secure; SameSite=Lax',
        'csrftoken=csrf-test; Path=/; HttpOnly; Secure; SameSite=Lax',
      ],
    });
    res.end(JSON.stringify({ ok: true }));
  });
  const backendUrl = await listen(backend);
  const app = createWebApp({
    backendUrl,
    trustRenderProxy,
    webHandler: (_req, res) => res.type('html').send('<html>Expo</html>'),
  });
  const web = createServer(app);
  const url = await listen(web);
  t.after(async () => { await close(web); await close(backend); });
  return { url, backend, received };
}

test('proxy conserva ruta, query, cuerpo JSON, Host, Origin, cookies y CSRF', async t => {
  const { url, received } = await fixture(t);
  const body = JSON.stringify({ usuario: 'prueba', contrasena: 'valor-de-prueba' });
  const response = await fetch(`${url}/api/sistema/app/login/?next=%2Fcrm`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json', Origin: url,
      Cookie: 'csrftoken=csrf-test', 'X-CSRFToken': 'csrf-test',
    },
    body,
  });
  assert.equal(response.status, 200);
  assert.equal(received.length, 1);
  assert.equal(received[0].url, '/api/sistema/app/login/?next=%2Fcrm');
  assert.equal(received[0].method, 'POST');
  assert.equal(received[0].body, body);
  assert.equal(received[0].headers.host, new URL(url).host);
  assert.equal(received[0].headers.origin, url);
  assert.equal(received[0].headers.cookie, 'csrftoken=csrf-test');
  assert.equal(received[0].headers['x-csrftoken'], 'csrf-test');
  assert.equal(response.headers.getSetCookie().length, 2);
  assert.match(response.headers.getSetCookie()[0], /HttpOnly; Secure; SameSite=Lax/);
});

test('solo rutas API, static y salud llegan a Django; navegacion llega a Expo', async t => {
  const { url, received } = await fixture(t);
  for (const route of ['/api/sistema/app/csrf/', '/healthz', '/static/admin/css/base.css']) {
    const response = await fetch(`${url}${route}`);
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type'), /json/);
  }
  for (const route of ['/login', '/proyectos/gestion', '/apiario', '/healthzone']) {
    const response = await fetch(`${url}${route}`);
    assert.equal(await response.text(), '<html>Expo</html>');
  }
  assert.equal(received.length, 3);
  const denied = await fetch(`${url}/api/fallo/`);
  assert.equal(denied.status, 401);
});

test('entrada directa ignora suplantacion de IP, host y HTTPS por cabeceras', async t => {
  const { url, received } = await fixture(t);
  await fetch(`${url}/healthz`, {
    headers: {
      'X-Internal-Client-IP': '198.51.100.40',
      'X-Forwarded-For': '198.51.100.41',
      'X-Forwarded-Proto': 'https',
      'X-Forwarded-Host': 'evil.example',
      Forwarded: 'for=198.51.100.42;proto=https',
      'CF-Connecting-IP': '198.51.100.43',
    },
  });
  const headers = received[0].headers;
  assert.equal(headers['x-internal-client-ip'], '127.0.0.1');
  assert.equal(headers['x-forwarded-proto'], 'http');
  assert.equal(headers['x-forwarded-for'], undefined);
  assert.equal(headers['x-forwarded-host'], undefined);
  assert.equal(headers.forwarded, undefined);
  assert.equal(headers['cf-connecting-ip'], undefined);
});

test('entrada Render confia solo en el salto mas cercano y normaliza HTTPS', async t => {
  const { url, received } = await fixture(t, { trustRenderProxy: true });
  await fetch(`${url}/healthz`, {
    headers: {
      'X-Forwarded-For': '198.51.100.99, 203.0.113.25',
      'X-Forwarded-Proto': 'https',
      'X-Internal-Client-IP': '198.51.100.99',
    },
  });
  assert.equal(received[0].headers['x-internal-client-ip'], '203.0.113.25');
  assert.equal(received[0].headers['x-forwarded-proto'], 'https');
});

test('si Django cae, salud y API devuelven 503 en lugar de una pagina Expo', async t => {
  const { url, backend } = await fixture(t);
  // Reservar y cerrar un puerto propio evita depender de servicios externos.
  await close(backend);
  for (const route of ['/healthz', '/api/sistema/app/yo/']) {
    const response = await fetch(`${url}${route}`);
    assert.equal(response.status, 503);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal((await response.json()).ok, false);
  }
});
