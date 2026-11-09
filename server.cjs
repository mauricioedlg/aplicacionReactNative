process.env.NODE_ENV = 'production';

const path = require('node:path');
const { isIP } = require('node:net');
const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const { createRequestHandler } = require('expo-server/adapter/express');

function createWebApp({
  backendUrl = process.env.DJANGO_INTERNAL_URL || 'http://127.0.0.1:8000',
  trustRenderProxy = process.env.RENDER === 'true',
  webHandler,
} = {}) {
  const app = express();
  app.disable('x-powered-by');
  // Render controla la entrada publica. Fuera de Render ignoramos las
  // cabeceras de proxy que pueda enviar un cliente directamente.
  app.set('trust proxy', trustRenderProxy ? 1 : false);

  app.use((req, _res, next) => {
    const clientIp = req.ip;
    const protocol = req.protocol === 'https' ? 'https' : 'http';
    // Nunca reenviar la identidad interna aportada por el navegador ni
    // cadenas de proxies arbitrarias. Host y Origin se conservan para CSRF.
    for (const header of Object.keys(req.headers)) {
      if (header.startsWith('x-forwarded-') || header === 'forwarded'
        || header === 'x-internal-client-ip' || header === 'cf-connecting-ip') {
        delete req.headers[header];
      }
    }
    req.headers['x-internal-client-ip'] = isIP(clientIp) ? clientIp : req.socket.remoteAddress;
    req.headers['x-forwarded-proto'] = protocol;
    next();
  });

  // Montar en la raiz conserva /api/ y los query params completos. No usar
  // express.json() antes del proxy: Django debe recibir el cuerpo intacto.
  app.use(createProxyMiddleware({
    target: backendUrl,
    changeOrigin: false,
    pathFilter: pathname => pathname === '/healthz' || /^\/(api|static)(\/|$)/.test(pathname),
    proxyTimeout: 65000,
    timeout: 70000,
    on: {
      error: (_error, _req, res) => {
        if (res.headersSent) {
          res.destroy();
          return;
        }
        res.writeHead(503, {
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'no-store',
        });
        res.end(JSON.stringify({ ok: false, mensaje: 'El servidor no esta disponible temporalmente.' }));
      },
    },
  }));

  app.use(express.static(path.join(__dirname, 'dist/client')));
  app.use(webHandler || createRequestHandler({
    build: path.join(__dirname, 'dist/server'),
  }));
  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT || 3000);
  const server = createWebApp().listen(port, '0.0.0.0', () => {
    console.log(`Servidor web iniciado en el puerto ${port}`);
  });

  // El supervisor del contenedor envia SIGTERM a ambos procesos.
  for (const signal of ['SIGTERM', 'SIGINT']) {
    process.once(signal, () => {
      server.close(() => process.exit(0));
      setTimeout(() => process.exit(1), 10000).unref();
    });
  }
}

module.exports = { createWebApp };
