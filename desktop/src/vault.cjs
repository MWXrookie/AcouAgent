'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const {atomicWrite} = require('./runtime.cjs');

class Vault {
  constructor(directory, safeStorage) {this.filename = path.join(directory, 'credentials.bin'); this.storage = safeStorage;}
  async load() {
    if (!this.storage.isEncryptionAvailable()) throw Error('system_encryption_unavailable');
    try {
      return JSON.parse(this.storage.decryptString(await fs.readFile(this.filename)));
    } catch (error) {
      // A damaged or foreign-user vault is not silently replaced: its resources stay owned.
      if (error.code !== 'ENOENT') throw Error('desktop_credentials_unreadable');
    }
    const value = Object.fromEntries(['MCP_AUTH_TOKEN', 'EXECUTOR_SHARED_TOKEN', 'PORTAL_ADMIN_TOKEN', 'ACOUAGENT_INSTALL_ID'].map(key => [key, crypto.randomBytes(32).toString('hex')]));
    await atomicWrite(this.filename, this.storage.encryptString(JSON.stringify(value)));
    return value;
  }
}
module.exports = {Vault};
