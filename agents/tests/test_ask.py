import ask


async def test_tools_capture_hops_in_code():
    captured = []

    async def call(name):
        return {"agent": name, "ok": True, "fact": "fact-" + name}

    tools = ask.make_tools(call, ["lab-finance", "lab-risk"], captured)
    assert [t.name for t in tools] == ["financial_snapshot", "risk_snapshot"]
    assert await tools[1].func() == "fact-lab-risk"
    assert await tools[0].func() == "fact-lab-finance"
    assert [h["agent"] for h in captured] == ["lab-risk", "lab-finance"]


async def test_tool_returns_error_text_when_specialist_fails():
    captured = []

    async def call(name):
        return {"agent": name, "ok": False, "error": "server_error", "fact": None}

    tools = ask.make_tools(call, ["lab-finance", "lab-risk"], captured)
    assert await tools[0].func() == "specialist lab-finance failed: server_error"
    assert captured[0]["ok"] is False
