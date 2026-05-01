// PM2 process manager config for the LinkedIn AI Employee persistent processes.
//
// Start all:    pm2 start pm2_ecosystem.config.js
// Stop all:     pm2 stop all
// Restart one:  pm2 restart orchestrator
// View logs:    pm2 logs
// Save & boot:  pm2 save && pm2 startup
//
// GitHub Actions handles the scheduled batch jobs (generate, publish, triage).
// This file manages the three processes that must run 24/7.

module.exports = {
  apps: [
    {
      name: "orchestrator",
      script: "orchestrator.py",
      interpreter: "python3",
      watch: false,
      restart_delay: 5000,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    },
    {
      name: "discord-bot",
      script: "tools/dm_ghostwriter.py",
      interpreter: "python3",
      watch: false,
      restart_delay: 5000,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    },
    {
      name: "discord-watcher",
      script: "watchers/discord_watcher.py",
      interpreter: "python3",
      watch: false,
      restart_delay: 5000,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: "1"
      }
    }
  ]
};
