"use strict";

const CACHE_NAME = "central-admin-shell-v5";
const APP_SHELL = [
  "/",
  "/index.html",
  "/admin",
  "/tienda.html",
  "/central.css",
  "/visual-refresh.css",
  "/admin-login.html",
  "/manifest.webmanifest",
  "/icon.svg",
  "/offline.html",
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(async cache => {
      await Promise.allSettled(
        APP_SHELL.map(url => cache.add(url).catch(() => {}))
      );
      return self.skipWaiting();
    })
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(key => key.startsWith("central-admin-shell-") && key !== CACHE_NAME)
          .map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate" || (request.headers.get("accept") && request.headers.get("accept").includes("text/html"))) {
    event.respondWith(
      fetch(request).then(async response => {
        if (response.ok) {
          const cache = await caches.open(CACHE_NAME);
          await cache.put(request, response.clone()).catch(() => {});
        }
        return response;
      }).catch(async () => {
        const cached = await caches.match(request);
        if (cached) return cached;
        const adminCached = await caches.match("/admin");
        if (adminCached) return adminCached;
        const indexCached = await caches.match("/index.html");
        if (indexCached) return indexCached;
        return caches.match("/offline.html");
      })
    );
    return;
  }

  // Network-first strategy for static assets
  event.respondWith(
    fetch(request).then(async response => {
      if (response.ok) {
        const cache = await caches.open(CACHE_NAME);
        await cache.put(request, response.clone()).catch(() => {});
      }
      return response;
    }).catch(() => caches.match(request))
  );
});
