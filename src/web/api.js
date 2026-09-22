/*
 * API 客户端 —— 只通过 /api/* 访问数据。
 *
 * 零构建: 本文件由 index.html 直接 <script> 引入, 无打包器、无 npm 依赖。
 * 加载顺序: api.js -> i18n.js -> app.js -> views/*.js
 */
'use strict';

// ---------------------------------------------------------------- API 客户端

const API_BASE = '/api';

class ApiError extends Error {
  constructor(error) {
    super((error && error.message) || 'request failed');
    this.code = (error && error.code) || 'INTERNAL_ERROR';
    this.detail = (error && error.detail) || null;
  }
}

async function api(path, options) {
  const opts = options || {};
  let url = API_BASE + path;
  if (opts.query) {
    const params = new URLSearchParams();
    Object.keys(opts.query).forEach((key) => {
      const value = opts.query[key];
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, String(value));
      }
    });
    const qs = params.toString();
    if (qs) url += '?' + qs;
  }
  const init = { method: opts.method || 'GET', headers: {} };
  if (opts.body !== undefined && !(opts.body instanceof FormData)) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  } else if (opts.body instanceof FormData) {
    init.body = opts.body;
  }
  let response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    throw new ApiError({ code: 'NETWORK_ERROR', message: t('无法连接本机服务: ') + err.message });
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    throw new ApiError({
      code: 'INTERNAL_ERROR',
      message: 'HTTP ' + response.status + t(': 响应不是合法 JSON'),
    });
  }
  if (!payload || payload.success !== true) {
    throw new ApiError((payload && payload.error) || { message: 'HTTP ' + response.status });
  }
  return payload.data;
}

// ------------------------------------------------------------------ 工具函数
