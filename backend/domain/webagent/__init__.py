"""The web agent: a separate agent that drives a real Chrome window for Jarvis.

    tools.py     web_task / web_reply / web_status / web_stop -- what Jarvis calls
    agent.py     WebAgent: its own model loop, one action per step, WebMCP first
    llm.py       the agent's model (ordinary Gemini API, tools can change per call)
    browser.py   BrowserSession: Playwright + installed Chrome, headed, own profile
    snapshot.*   the page as numbered refs the model can act on
    webmcp.py    a site's own tools, via document.modelContext.getTools/executeTool
    safety.py    risky clicks need a yes; secrets are never typed; blocklist
"""
