module.exports = {
  apps: [{
    name: "nats-mcp",
    script: "/home/ted/repos/personal/nats-mcp/.venv/bin/python",
    args: "-m nats_mcp.server",
    cwd: "/home/ted/repos/personal/nats-mcp",
    interpreter: "none",

    restart_delay: 5000,
    max_restarts: 10,
    min_uptime: "10s",

    out_file: "/home/ted/logs/nats-mcp-out.log",
    error_file: "/home/ted/logs/nats-mcp-error.log",
    log_file: "/home/ted/logs/nats-mcp.log",
    merge_logs: true,
    time: true,

    env: {
      NATS_MONITOR_URL: "http://localhost:8222",
    },
  }],
};
