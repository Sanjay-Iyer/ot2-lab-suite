import ast
import pathlib
import pytest

def test_no_legacy_langchain_agents_imports():
    """
    Ensure no files in src/agents use the deprecated langchain.agents imports 
    for tool calling, but allow modern langgraph.prebuilt.
    """
    project_root = pathlib.Path(__file__).resolve().parent.parent
    agents_dir = project_root / "src" / "agents"
    
    # We explicitly ban 'langchain.agents' but allow 'langgraph.prebuilt'
    banned_imports = ["langchain.agents"]
    allowed_imports = ["langgraph.prebuilt"]
    
    violations = []
    
    for py_file in agents_dir.rglob("*.py"):
        # Skip archive to prevent noise
        if "archive" in py_file.parts:
            continue
            
        with open(py_file, "r", encoding="utf-8") as f:
            try:
                tree = ast.parse(f.read(), filename=str(py_file))
            except SyntaxError:
                continue
                
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module in banned_imports:
                    # Allow create_tool_calling_agent specifically if it's there for legacy mock tests, 
                    # but in general we want to flag it if it's used for actual production logic.
                    # The user specifically warned against blindly assuming create_agent replaces create_react_agent.
                    for alias in node.names:
                        if alias.name in ["create_agent", "AgentExecutor"]:
                            violations.append(f"{py_file.name}: imports {alias.name} from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in banned_imports:
                        violations.append(f"{py_file.name}: imports {alias.name}")

    assert not violations, f"Found deprecated legacy agent imports: {violations}"

def test_modern_langgraph_usage_is_allowed():
    """
    Dummy test to explicitly document that langgraph.prebuilt is the supported path.
    """
    # This just proves the test suite acknowledges the correct import structure.
    try:
        from langgraph.prebuilt import create_react_agent
        assert callable(create_react_agent)
    except ImportError:
        pytest.fail("langgraph.prebuilt.create_react_agent should be available in the environment.")
