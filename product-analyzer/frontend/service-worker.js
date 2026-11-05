"use strict";

const CACHE_NAME = "central-admin-shell-v4";
const APP_SHELL = [
  "/",
  "/index.html",
  "/admin",
  "/central.css",
  "/admin-login.html",
  "/manifest.webmanifest",
  "/icon.svg",
  "/offline.html",
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
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

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).then(async response => {
        if (response.ok) {
          await caches.open(CACHE_NAME).then(cache => cache.put(request, response.clone())).catch(() => {});
        }
        return response;
      }).catch(async () =>
        (await caches.match(request)) ||
        (await caches.match("/index.html")) ||
        (await caches.match("/offline.html"))
      )
    );
    return;
  }

  // Network-first strategy for static assets to ensure code updates reflect immediately
  event.respondWith(
    fetch(request).then(async response => {
      if (response.ok) {
        await caches.open(CACHE_NAME).then(cache => cache.put(request, response.clone())).catch(() => {});
      }
      return response;
    }).catch(() => caches.match(request))
  );
});
