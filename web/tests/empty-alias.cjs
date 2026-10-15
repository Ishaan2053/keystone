const fs = require('fs');
const Module = require('module');
const path = require('path');

const root = path.resolve(__dirname, '..', '.empty-run');
const originalResolveFilename = Module._resolveFilename;
Module._resolveFilename = function resolveFilename(request, parent, isMain, options) {
  if (typeof request === 'string' && request.startsWith('@/')) {
    const mapped = path.join(root, request.slice(2));
    if (fs.existsSync(mapped)) request = mapped;
    else for (const ext of ['.js', '.json', '.cjs', '.mjs']) {
      if (fs.existsSync(mapped + ext)) { request = mapped + ext; break; }
    }
  }
  return originalResolveFilename.call(this, request, parent, isMain, options);
};