# AVG's TLS interception on this workstation replaces server certificates
# with ones signed by its own local CA, which certifi-based verification
# rejects (RISK-REGISTER.md R8). Routing TLS verification through the
# Windows certificate store fixes it - call this once at the entrypoint of
# any tool that makes live HTTPS calls. No-op if truststore isn't installed.


def enable_system_truststore():
    try:
        import truststore
    except ImportError:
        return False
    truststore.inject_into_ssl()
    return True
