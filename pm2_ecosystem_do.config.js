module.exports = {
  apps: [
    {
      name: "orchestrator",
      script: "orchestrator.py",
      interpreter: "/home/usman/linkedin-automation/.venv/bin/python3",
      cwd: "/home/usman/linkedin-automation",
      env_file: "/home/usman/linkedin-automation/.env",
      env: {
        PYTHONPATH: "/home/usman/linkedin-automation"
      },
      max_memory_restart: "400M",
      restart_delay: 5000,
      max_restarts: 10,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file: "/home/usman/logs/orchestrator-error.log",
      out_file: "/home/usman/logs/orchestrator-out.log"
    },
    {
      name: "discord-bot",
      script: "tools/dm_ghostwriter.py",
      interpreter: "/home/usman/linkedin-automation/.venv/bin/python3",
      cwd: "/home/usman/linkedin-automation",
      env_file: "/home/usman/linkedin-automation/.env",
      env: {
        PYTHONPATH: "/home/usman/linkedin-automation"
      },
      max_memory_restart: "300M",
      restart_delay: 5000,
      max_restarts: 10,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file: "/home/usman/logs/discord-bot-error.log",
      out_file: "/home/usman/logs/discord-bot-out.log"
    },
    {
      name: "discord-watcher",
      script: "watchers/discord_watcher.py",
      interpreter: "/home/usman/linkedin-automation/.venv/bin/python3",
      cwd: "/home/usman/linkedin-automation",
      env_file: "/home/usman/linkedin-automation/.env",
      env: {
        PYTHONPATH: "/home/usman/linkedin-automation"
      },
      max_memory_restart: "200M",
      restart_delay: 5000,
      max_restarts: 10,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file: "/home/usman/logs/discord-watcher-error.log",
      out_file: "/home/usman/logs/discord-watcher-out.log"
    }
  ]
};
