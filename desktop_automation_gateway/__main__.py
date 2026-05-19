# -*- coding: utf-8 -*-
import uvicorn

if __name__ == "__main__":
    port = int(__import__("os").environ.get("DESKTOP_AGENT_GATE_PORT", "8766"))
    uvicorn.run("desktop_automation_gateway.main:app", host="0.0.0.0", port=port)
