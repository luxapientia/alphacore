/**
 * pm2.miner.config.js — PM2 ecosystem config for AlphaCore miner
 *
 * Usage:
 *   pm2 start scripts/miner/process/pm2.miner.config.js --name miner-myhot-test --update-env
 *   pm2 logs miner-myhot-test
 */
const path = require('path');

const repoRoot = path.resolve(__dirname, '../../..');
const startScript = path.resolve(__dirname, './start_miner_pm2.sh');

module.exports = {
  apps: [
    {
      name: process.env.PROCESS_NAME || 'alphacore-miner',
      script: startScript,
      interpreter: '/bin/bash',
      cwd: repoRoot,
      env: {
        ENTRYPOINT: process.env.ENTRYPOINT || 'neurons/miner.py',
        VENV_DIR: process.env.VENV_DIR || path.join(repoRoot, 'venv'),
        ENV_FILE: process.env.ENV_FILE || '',
        LOG_DIR: process.env.LOG_DIR || path.join(repoRoot, 'logs', 'pm2'),
      },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      error_file: path.join(process.env.LOG_DIR || path.join(repoRoot, 'logs', 'pm2'), `${process.env.PROCESS_NAME || 'alphacore-miner'}.error.log`),
      out_file: path.join(process.env.LOG_DIR || path.join(repoRoot, 'logs', 'pm2'), `${process.env.PROCESS_NAME || 'alphacore-miner'}.out.log`),
      merge_logs: true,
    },
  ],
};

