from workers.malware_scan import key_from_uri

def test_blob_key_parse():
    assert key_from_uri("https://acct.blob.core.windows.net/dealos-quarantine/t/d/f")=="t/d/f"
