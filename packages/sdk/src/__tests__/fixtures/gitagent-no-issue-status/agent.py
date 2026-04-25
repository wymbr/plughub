# agent.py — GitAgent without issue_status (contract violation)
def agent_login(ctx):
    pass

def agent_ready(ctx):
    pass

def agent_busy(ctx):
    pass

def agent_done(ctx):
    # BUG: required field missing — violates the execution contract (spec 4.2)
    ctx.report({
        "outcome": "resolved"
    })

def agent_pause(ctx):
    pass

def agent_logout(ctx):
    pass
