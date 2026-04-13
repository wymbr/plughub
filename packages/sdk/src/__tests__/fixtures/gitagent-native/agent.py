# agent.py — native PlugHub agent

def agent_login(ctx):
    pass

def agent_ready(ctx):
    pass

def agent_busy(ctx):
    pass

def agent_done(ctx):
    ctx.report({
        "issue_status": "Concluído",
        "outcome": "resolved"
    })

def agent_pause(ctx):
    pass

def agent_logout(ctx):
    pass
