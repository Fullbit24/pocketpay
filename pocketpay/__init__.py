# pocketpay — TechX Dubai'26 AI Agent Arena, Domain 1: FinTech Fraud & Compliance
# PocketPay: Autonomous Campus Wallet & Scam Defense Agent

# lazy on purpose: keeps `python -m pocketpay.db` etc. warning-free
def __getattr__(name):
    if name == "PocketPayAgent":
        from .agent import PocketPayAgent
        return PocketPayAgent
    if name in ("db", "guard", "tools"):
        import importlib
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(name)
