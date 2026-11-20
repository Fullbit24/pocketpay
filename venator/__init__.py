# venator — TechX Dubai'26 AI Agent Arena, Domain 1: FinTech Fraud & Compliance
# Venator: Autonomous Campus Wallet & Scam Defense Agent

# lazy on purpose: keeps `python -m venator.db` etc. warning-free
def __getattr__(name):
    if name == "VenatorAgent":
        from .agent import VenatorAgent
        return VenatorAgent
    if name in ("db", "guard", "tools"):
        import importlib
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(name)
