"""
Phase 1: rich (address-level) synthetic transactions, GeoIP and multi-format ingestion.
No test needs the internet. GeoIP is a small stand-in; the one test that uses the real DB-IP files is skipped if they are absent.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, inspect, select

from backend.config import PROJECT_ROOT
from backend.ingestion import rich_formats as rf
from backend.ingestion.rich_formats import FormatError, RichTransactionIn, parse, validate_records
from backend.models import Base, FlowRecord, NetworkObservation, Transaction, TxDetails, TxInput, TxOutput
from backend.services.geoip import GeoIP

RICH_DIR = PROJECT_ROOT / "dataset" / "rich"
SAMPLES = {"json": "sample.json", "jsonl": "sample.jsonl", "csv": "sample.csv", "xml": "sample.xml"}
COUNTRY_DB = PROJECT_ROOT / "data" / "geoip" / "dbip-country-lite.mmdb"
ASN_DB = PROJECT_ROOT / "data" / "geoip" / "dbip-asn-lite.mmdb"


# ---- a small GeoIP stand-in ---------------------------------------------------------------------------------------------
class StubReader:
    def __init__(self, table: dict) -> None:
        self.table = table

    def get(self, ip: str):
        return next((rec for prefix, rec in self.table.items() if ip.startswith(prefix)), None)

    def metadata(self):
        return SimpleNamespace(database_type="Stub", build_epoch=1_788_000_000)

    def close(self) -> None:
        pass


def stub_geo() -> GeoIP:
    country = StubReader({"81.": {"country": {"iso_code": "DE", "names": {"en": "Germany"}}}, "93.": {"country": {"iso_code": "US", "names": {"en": "United States"}}}})
    asn = StubReader({"81.": {"autonomous_system_number": 64500, "autonomous_system_organization": "Stub DE Net"},
                      "93.": {"autonomous_system_number": 64501, "autonomous_system_organization": "Stub US Net"}})
    return GeoIP(country_reader=country, asn_reader=asn)


# ---- building records --------------------------------------------------------------------------------------------------------
SAT = 100_000_000


def txid_of(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def rich_for(tx: dict, *, flow: bool = True, geo: bool = False) -> dict:
    """A balanced rich record for a stored transaction (uses its amount and its input/output counts)."""
    amount = round(tx["amount_btc"] * SAT)
    n_in, n_out = tx["input_count"], tx["output_count"]
    changes = [10_000 + i for i in range(n_out - 1)]
    fee = 1_500
    total = amount + sum(changes) + fee
    parts = [total // n_in] * n_in
    parts[-1] += total - sum(parts)
    sid = tx["transaction_id"]
    rec = {
        "timestamp": tx["timestamp"], "txid": txid_of(sid), "legacy_transaction_id": sid,
        "sender_wallet": tx["sender_wallet"], "receiver_wallet": tx["receiver_wallet"], "amount_btc": tx["amount_btc"],
        "fee_btc": fee / SAT, "script_type": "p2wpkh",
        "input_addresses": [f"sy1qin{sid[-3:]}{i}" for i in range(n_in)], "input_amounts": [p / SAT for p in parts],
        "output_addresses": [f"sy1qpay{sid[-3:]}"] + [f"sy1qchg{sid[-3:]}{i}" for i in range(n_out - 1)],
        "output_amounts": [amount / SAT] + [c / SAT for c in changes],
    }
    if flow:
        rec.update({"src_ip": "81.2.3.4", "dst_ip": "93.184.216.34", "src_port": 51000, "dst_port": 8333})
    if geo:
        rec.update({"geo_country": "DE", "asn": 64500, "asn_org": "Stub DE Net"})
    return rec


def as_jsonl(records: list[dict]) -> bytes:
    return ("\n".join(json.dumps(r) for r in records) + "\n").encode()


def as_json(records: list[dict]) -> bytes:
    return json.dumps(records).encode()


def as_csv(records: list[dict]) -> bytes:
    columns = list(dict.fromkeys(k for r in records for k in r))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in records:
        w.writerow([json.dumps(r[k]) if isinstance(r.get(k), list) else ("" if r.get(k) is None else r[k]) for k in columns])
    return buf.getvalue().encode()


def as_xml(records: list[dict]) -> bytes:
    root = ET.Element("transactions")
    for r in records:
        tx = ET.SubElement(root, "transaction")
        for k, v in r.items():
            el = ET.SubElement(tx, k)
            if isinstance(v, list):
                for x in v:
                    ET.SubElement(el, "address" if k.endswith("addresses") else "amount").text = str(x)
            elif v is not None:
                el.text = str(v)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


FORMAT_WRITERS = {"jsonl": as_jsonl, "json": as_json, "csv": as_csv, "xml": as_xml}


def valid_record(**overrides) -> dict:
    rec = {"timestamp": "2026-01-01T00:00:00Z", "txid": txid_of("x"), "fee_btc": 0.00001, "script_type": "p2wpkh",
           "input_addresses": ["a1", "a2"], "input_amounts": [0.6, 0.40001], "output_addresses": ["b1", "b2"], "output_amounts": [0.9, 0.1]}
    rec.update(overrides)
    return rec


def check(rec: dict) -> RichTransactionIn:
    (index, model), = validate_records([(0, rec)])[0]
    return model


def reason(rec: dict) -> str:
    valid, rejected = validate_records([(0, rec)])
    assert not valid and len(rejected) == 1, rejected
    return rejected[0]["error"]


# ---- record validation ------------------------------------------------------------------------------------------------------------
def test_a_valid_record_passes_and_is_normalised():
    m = check(valid_record(txid=txid_of("x").upper(), script_type="P2WPKH", src_ip="81.2.3.4", geo_country="de", timestamp="2026-01-01T02:00:00+02:00"))
    assert m.txid == txid_of("x") and m.script_type == "p2wpkh" and m.geo_country == "DE" and m.timestamp.isoformat() == "2026-01-01T00:00:00"


@pytest.mark.parametrize("change,fragment", [
    ({"txid": "abc"}, "64 hexadecimal"),
    ({"txid": "z" * 64}, "64 hexadecimal"),
    ({"script_type": "p2xx"}, "script_type must be one of"),
    ({"fee_btc": -0.1}, "fee_btc"),
    ({"input_amounts": [1.0]}, "differ in length"),
    ({"output_addresses": ["b1"]}, "differ in length"),
    ({"input_addresses": [], "input_amounts": []}, "input_addresses"),
    ({"output_amounts": [0.9, 0.0]}, "at least 1 satoshi"),
    ({"output_amounts": [0.9, -0.1]}, "at least 1 satoshi"),
    ({"output_amounts": [0.9, 0.000000001]}, "at least 1 satoshi"),
    ({"output_amounts": [0.9, 0.2]}, "does not balance"),
    ({"input_addresses": ["a 1", "a2"]}, "addresses must be"),
    ({"src_ip": "999.1.1.1"}, "not a valid IP"),
    ({"dst_ip": "not-an-ip"}, "not a valid IP"),
    ({"src_port": 70000}, "src_port"),
    ({"dst_port": -1}, "dst_port"),
    ({"geo_country": "DEU"}, "geo_country"),
    ({"amount_btc": 0.5}, "must equal one of the output amounts"),
    ({"sender_wallet": "w1", "receiver_wallet": "w1"}, "must differ"),
    ({"surprise": 1}, "surprise"),
    ({"timestamp": "yesterday"}, "timestamp"),
])
def test_invalid_records_are_rejected_with_a_reason(change, fragment):
    assert fragment in reason(valid_record(**change))


def test_the_balance_check_allows_one_satoshi_and_no_more():
    rec = valid_record(input_amounts=[0.6, 0.40001], output_amounts=[0.9, 0.1], fee_btc=0.00001)
    check(rec)                                                                     # exact
    check({**rec, "fee_btc": 0.00001000 + 0.00000001})                             # 1 sat off
    assert "does not balance" in reason({**rec, "fee_btc": 0.00001 + 0.00000002})  # 2 sat off


def test_ipv6_is_accepted_and_empty_optional_cells_mean_not_given():
    m = check(valid_record(src_ip="2001:db8::1", dst_ip="", src_port="", asn=""))
    assert m.src_ip == "2001:db8::1" and m.dst_ip is None and m.src_port is None and m.asn is None and m.has_flow


# ---- parsers ---------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("fmt", ["jsonl", "json", "csv", "xml"])
def test_all_four_formats_parse_to_the_same_records(fmt):
    records = [valid_record(txid=txid_of(str(i)), src_ip="81.2.3.4", src_port=40000 + i, asn=64500 + i) for i in range(3)]
    raw, rejected = parse(FORMAT_WRITERS[fmt](records), fmt)
    valid, invalid = validate_records(raw)
    assert not rejected and not invalid and [i for i, _ in valid] == [0, 1, 2]
    assert [m.txid for _, m in valid] == [r["txid"] for r in records] and [m.src_port for _, m in valid] == [40000, 40001, 40002]
    assert all(m.input_addresses == ["a1", "a2"] and m.output_amounts == [0.9, 0.1] for _, m in valid)


@pytest.mark.skipif(not all((RICH_DIR / n).is_file() for n in SAMPLES.values()), reason="sample files not generated")
def test_the_generated_sample_files_agree_across_all_formats():
    parsed = {}
    for fmt, name in SAMPLES.items():
        raw, rejected = parse((RICH_DIR / name).read_bytes(), fmt)
        valid, invalid = validate_records(raw)
        assert not rejected and not invalid, (fmt, rejected, invalid)
        parsed[fmt] = [m.model_dump() for _, m in valid]
    assert len(parsed["json"]) == 20 and parsed["json"] == parsed["jsonl"] == parsed["csv"] == parsed["xml"]


def test_json_accepts_an_object_wrapper_and_rejects_other_shapes():
    assert len(parse(json.dumps({"transactions": [valid_record()]}), "json")[0]) == 1
    for bad in ("{not json", json.dumps({"a": 1}), json.dumps("text"), ""):
        with pytest.raises(FormatError):
            parse(bad, "json")
    raw, rejected = parse(json.dumps([valid_record(), 5, "x"]), "json")
    assert len(raw) == 1 and [r["index"] for r in rejected] == [1, 2]


def test_jsonl_reports_bad_lines_one_by_one_and_skips_blank_lines():
    text = json.dumps(valid_record()) + "\n\n{broken\n" + json.dumps(valid_record(txid=txid_of("2"))) + "\n[1]\n"
    raw, rejected = parse(text, "jsonl")
    assert [i for i, _ in raw] == [0, 2] and [(r["index"], r["error"]) for r in rejected] == [(1, "line is not valid JSON"), (3, "record must be a JSON object")]


def test_csv_needs_its_columns_and_reports_bad_rows():
    with pytest.raises(FormatError, match="missing required column"):
        parse("txid,timestamp\nabc,2026-01-01\n", "csv")
    header = "timestamp,txid,fee_btc,script_type,input_addresses,input_amounts,output_addresses,output_amounts\n"
    good = f'2026-01-01T00:00:00Z,{txid_of("1")},0.00001,p2wpkh,"[""a""]","[1.0]","[""b""]","[0.99999]"\n'
    bad_list = f'2026-01-01T00:00:00Z,{txid_of("2")},0.00001,p2wpkh,not-json,"[1.0]","[""b""]","[0.99999]"\n'
    short = "2026-01-01T00:00:00Z,abc\n"
    raw, rejected = parse(header + good + bad_list + short, "csv")
    assert [i for i, _ in raw] == [0] and [r["index"] for r in rejected] == [1, 2]
    assert len(validate_records(raw)[0]) == 1


def test_xml_needs_the_right_shape():
    for bad in ("<transactions><transaction>", "<other/>", ""):
        with pytest.raises(FormatError):
            parse(bad, "xml")
    raw, rejected = parse("<transactions><oops/></transactions>", "xml")
    assert not raw and rejected[0]["index"] == 0
    raw, rejected = parse("<transactions><transaction><input_amounts><amount>x</amount></input_amounts></transaction></transactions>", "xml")
    assert not raw and "not a number" in rejected[0]["error"]


@pytest.mark.parametrize("payload", [
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]><transactions>&lol2;</transactions>',
    '<?xml version="1.0"?><!DOCTYPE t [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><transactions><transaction><txid>&xxe;</txid></transaction></transactions>',
    '<?xml version="1.0"?><!DOCTYPE t SYSTEM "http://127.0.0.1:9/evil.dtd"><transactions/>',
])
def test_xml_entity_and_dtd_attacks_are_rejected(payload):
    with pytest.raises(FormatError, match="XML rejected"):
        parse(payload, "xml")


def test_bad_bytes_unknown_formats_and_too_many_records():
    with pytest.raises(FormatError, match="UTF-8"):
        parse(b"\xff\xfe\x00bad", "jsonl")
    with pytest.raises(FormatError, match="format must be"):
        parse("{}", "yaml")
    with pytest.raises(FormatError, match="more than"):
        big = "\n".join(json.dumps(valid_record()) for _ in range(rf.MAX_RECORDS + 1))
        parse(big, "jsonl")


# ---- the GeoIP lookup -----------------------------------------------------------------------------------------------------------------
def test_lookup_fills_country_and_asn_and_caches():
    geo = stub_geo()
    assert geo.lookup("81.2.3.4") == {"country_code": "DE", "country_name": "Germany", "asn": 64500, "asn_org": "Stub DE Net"}
    assert geo.lookup("81.2.3.4") == geo.lookup("81.2.3.4")
    assert geo.available and geo.status()["complete"] and geo.status()["attribution"] == "IP geolocation by DB-IP.com"


@pytest.mark.parametrize("ip", [None, "", "not-an-ip", "10.0.0.1", "192.168.1.1", "127.0.0.1", "203.0.113.5", "8.8.8.8x"])
def test_lookup_gives_empty_values_for_private_reserved_or_unknown_addresses(ip):
    assert set(stub_geo().lookup(ip).values()) == {None}


def test_missing_files_are_not_an_error(tmp_path):
    geo = GeoIP(tmp_path / "nope-country.mmdb", tmp_path / "nope-asn.mmdb")
    assert geo.lookup("8.8.8.8") == {"country_code": None, "country_name": None, "asn": None, "asn_org": None}
    st = geo.status()
    assert st["available"] is False and "not available" in st["message"] and all(d["error"] == "file not found" for d in st["databases"])
    assert "IP geolocation by DB-IP.com" == st["attribution"]


def test_a_corrupt_file_is_not_an_error(tmp_path):
    bad = tmp_path / "bad.mmdb"
    bad.write_bytes(b"this is not a database")
    geo = GeoIP(bad, bad)
    assert geo.lookup("8.8.8.8")["country_code"] is None and geo.available is False


@pytest.mark.skipif(not (COUNTRY_DB.is_file() and ASN_DB.is_file()), reason="DB-IP files not present")
def test_the_real_dbip_files_answer_and_match_the_generated_dataset():
    geo = GeoIP(COUNTRY_DB, ASN_DB)
    google = geo.lookup("8.8.8.8")
    assert google["country_code"] == "US" and google["asn"] == 15169 and "Google" in google["asn_org"]
    assert geo.status()["complete"] and all(d["build_date"] for d in geo.status()["databases"])
    with open(RICH_DIR / "synthetic_rich_transactions.jsonl", encoding="utf-8") as fh:
        for _, line in zip(range(300), fh):
            r = json.loads(line)
            info = geo.lookup(r["src_ip"])
            assert (info["country_code"], info["asn"], info["asn_org"]) == (r["geo_country"], r["asn"], r["asn_org"])


# ---- API: enrichment import ------------------------------------------------------------------------------------------------------------
@pytest.fixture
def rich_client(loaded_client):
    loaded_client.app.state.geoip = stub_geo()
    return loaded_client


def stored(client) -> list[dict]:
    return client.get("/api/transactions", params={"limit": 500, "sort": "timestamp", "order": "asc"}).json()["items"]


def post(client, records, fmt="jsonl"):
    return client.post("/api/import/rich", params={"format": fmt}, content=FORMAT_WRITERS[fmt](records))


def count(db, model) -> int:
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_records_of_existing_transactions_are_attached_not_duplicated(rich_client):
    txs = stored(rich_client)
    before = count(rich_client.app.state.db, Transaction)
    r = post(rich_client, [rich_for(t) for t in txs])
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["received"], out["attached"], out["created"], out["duplicates"], out["rejected"]) == (len(txs), len(txs), 0, 0, [])
    assert out["details_total"] == len(txs) and "not observed traffic" in out["note"]
    db = rich_client.app.state.db
    assert count(db, Transaction) == before                                          # no second transaction was created
    assert count(db, TxDetails) == len(txs) and count(db, FlowRecord) == len(txs)
    assert count(db, TxInput) == sum(t["input_count"] for t in txs) and count(db, TxOutput) == sum(t["output_count"] for t in txs)


def test_details_endpoint_shows_addresses_flow_and_geo_filled_from_the_lookup(rich_client):
    tx = stored(rich_client)[0]
    post(rich_client, [rich_for(tx)])
    d = rich_client.get(f"/api/transactions/{tx['transaction_id']}/details").json()
    assert d["txid"] == txid_of(tx["transaction_id"]) and d["script_type"] == "p2wpkh" and d["fee_btc"] == 1500 / SAT and d["source"] == "synthetic"
    assert [i["position"] for i in d["inputs"]] == list(range(tx["input_count"])) and len(d["outputs"]) == tx["output_count"]
    assert round(sum(i["amount_btc"] for i in d["inputs"]), 8) == round(sum(o["amount_btc"] for o in d["outputs"]) + d["fee_btc"], 8)
    f = d["flow"]
    assert (f["src_ip"], f["dst_ip"], f["src_port"], f["dst_port"]) == ("81.2.3.4", "93.184.216.34", 51000, 8333)
    assert (f["geo_country"], f["asn"], f["asn_org"]) == ("DE", 64500, "Stub DE Net")            # filled from the lookup
    assert f["is_synthetic"] is True and f["origin"] == "synthetic_flow_record" and "not observed traffic" in d["note"]


def test_details_endpoint_errors(rich_client):
    tx = stored(rich_client)[0]["transaction_id"]
    assert rich_client.get(f"/api/transactions/{tx}/details").json()["error"]["code"] == "no_details"
    assert rich_client.get("/api/transactions/syn-999999/details").json()["error"]["code"] == "not_found"


def test_reimporting_changes_nothing(rich_client):
    records = [rich_for(t) for t in stored(rich_client)]
    post(rich_client, records)
    db = rich_client.app.state.db
    counts = [count(db, m) for m in (Transaction, TxDetails, TxInput, TxOutput, FlowRecord)]
    out = post(rich_client, records).json()
    assert (out["attached"], out["created"], out["duplicates"], out["rejected"]) == (0, 0, len(records), [])
    assert [count(db, m) for m in (Transaction, TxDetails, TxInput, TxOutput, FlowRecord)] == counts


def test_a_known_txid_with_different_content_is_rejected_not_overwritten(rich_client):
    rec = rich_for(stored(rich_client)[0])
    post(rich_client, [rec])
    changed = {**rec, "script_type": "p2tr"}
    out = post(rich_client, [changed]).json()
    assert out["attached"] == 0 and "different content" in out["rejected"][0]["error"]
    assert rich_client.get(f"/api/transactions/{rec['legacy_transaction_id']}/details").json()["script_type"] == "p2wpkh"


def test_duplicate_txids_inside_one_request(rich_client):
    rec = rich_for(stored(rich_client)[0])
    delta = round(0.0002 - rec["fee_btc"], 8)                                        # a different fee, still balanced
    different = {**rec, "fee_btc": 0.0002, "input_amounts": [round(rec["input_amounts"][0] + delta, 8)] + rec["input_amounts"][1:]}
    out = post(rich_client, [rec, rec, different]).json()
    assert (out["attached"], out["duplicates"]) == (1, 1) and "appears twice" in out["rejected"][0]["error"] and out["rejected"][0]["index"] == 2


def test_a_record_that_contradicts_the_stored_transaction_is_rejected(rich_client):
    tx = stored(rich_client)[0]
    for change, fragment in (({"receiver_wallet": "wallet_Z"}, "receiver"), ({"sender_wallet": "wallet_Z"}, "sender"), ({"timestamp": "2030-01-01T00:00:00Z"}, "timestamp")):
        out = post(rich_client, [{**rich_for(tx), **change}]).json()
        assert out["attached"] == 0 and fragment in out["rejected"][0]["error"], out
    out = post(rich_client, [{**rich_for(tx), "amount_btc": 0.4999, "output_amounts": [0.4999] + rich_for(tx)["output_amounts"][1:], "input_amounts": [rich_for(tx)["input_amounts"][0] - 0.0001] + rich_for(tx)["input_amounts"][1:]}]).json()
    assert "amount" in out["rejected"][0]["error"]
    assert count(rich_client.app.state.db, TxDetails) == 0


def test_a_transaction_that_already_has_other_details_is_not_given_a_second_set(rich_client):
    rec = rich_for(stored(rich_client)[0])
    post(rich_client, [rec])
    out = post(rich_client, [{**rec, "txid": txid_of("another")}]).json()
    assert "already has details" in out["rejected"][0]["error"]


def test_records_of_unknown_transactions_create_the_flat_transaction_through_the_normal_ingest(rich_client):
    db = rich_client.app.state.db
    before = count(db, Transaction)
    obs_before = count(db, NetworkObservation)
    new = {"timestamp": "2026-09-25T10:00:00Z", "txid": txid_of("new"), "sender_wallet": "wallet_A", "receiver_wallet": "wallet_E", "amount_btc": 0.07, "fee_btc": 0.00002,
          "script_type": "p2tr", "input_addresses": ["sy1pin"], "input_amounts": [0.07002], "output_addresses": ["sy1ppay"], "output_amounts": [0.07],
          "src_ip": "93.184.216.34", "dst_ip": "81.2.3.4", "src_port": 45000, "dst_port": 8333}
    no_wallets = {**new, "txid": txid_of("bare"), "sender_wallet": None, "receiver_wallet": None, "amount_btc": None}
    out = post(rich_client, [new, no_wallets]).json()
    assert (out["created"], out["attached"]) == (1, 0) and out["rejected"][0]["index"] == 1 and "required to create" in out["rejected"][0]["error"]
    assert count(db, Transaction) == before + 1 and count(db, TxDetails) == 1
    assert count(db, NetworkObservation) == obs_before                              # the flow record is the network layer: no legacy observation
    with db.session() as s:
        t = s.scalars(select(Transaction).where(Transaction.source_ref == f"rich:{txid_of('new')}")).one()
        assert (t.source, t.sender_wallet, t.receiver_wallet, t.amount_btc, t.input_count, t.output_count) == ("synthetic", "wallet_A", "wallet_E", 0.07, 1, 1)
        assert t.transaction_id.startswith("syn-")
    assert rich_client.get(f"/api/transactions/{t.transaction_id}/details").json()["flow"]["geo_country"] == "US"


def test_provided_geo_values_are_kept_and_differences_are_warnings(rich_client):
    tx = stored(rich_client)[0]
    rec = {**rich_for(tx), "geo_country": "FR", "asn": 64500, "asn_org": "Stub DE Net"}                       # country disagrees with the lookup (DE)
    out = post(rich_client, [rec]).json()
    assert out["attached"] == 1 and len(out["warnings"]) == 1 and "geo_country" in out["warnings"][0]["warning"]
    assert rich_client.get(f"/api/transactions/{tx['transaction_id']}/details").json()["flow"]["geo_country"] == "FR"


def test_count_differences_are_warnings_and_do_not_change_the_stored_transaction(rich_client):
    tx = next(t for t in stored(rich_client) if t["output_count"] == 1)
    rec = rich_for(tx)
    rec["output_addresses"] += ["sy1qextra"]
    rec["output_amounts"] += [0.0001]
    rec["input_amounts"][0] = round(rec["input_amounts"][0] + 0.0001, 8)
    out = post(rich_client, [rec]).json()
    assert out["attached"] == 1 and "stored counts kept" in out["warnings"][0]["warning"]
    assert next(t for t in stored(rich_client) if t["transaction_id"] == tx["transaction_id"])["output_count"] == 1


@pytest.mark.parametrize("fmt", ["jsonl", "json", "csv", "xml"])
def test_every_format_imports_through_the_api(rich_client, fmt):
    txs = stored(rich_client)
    out = post(rich_client, [rich_for(t, geo=True) for t in txs], fmt).json()
    assert (out["attached"], out["rejected"], out["warnings"]) == (len(txs), [], []) and out["format"] == fmt
    assert rich_client.get(f"/api/transactions/{txs[0]['transaction_id']}/details").json()["flow"]["asn"] == 64500


def test_bad_records_are_reported_by_index_and_the_good_ones_are_saved(rich_client):
    txs = stored(rich_client)[:3]
    records = [rich_for(txs[0]), {**rich_for(txs[1]), "txid": "bad"}, rich_for(txs[2])]
    out = post(rich_client, records).json()
    assert out["received"] == 3 and out["attached"] == 2 and [r["index"] for r in out["rejected"]] == [1] and "64 hexadecimal" in out["rejected"][0]["error"]


def test_unusable_bodies_get_clear_errors_and_change_nothing(rich_client):
    db = rich_client.app.state.db
    bad_format = rich_client.post("/api/import/rich", params={"format": "yaml"}, content=b"{}")
    assert bad_format.status_code == 422 and bad_format.json()["error"]["code"] == "invalid_format"
    assert rich_client.post("/api/import/rich", content=b"{}").status_code == 422              # format is required
    r = rich_client.post("/api/import/rich", params={"format": "json"}, content=b"{not json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_format"
    evil = b'<?xml version="1.0"?><!DOCTYPE t [<!ENTITY x SYSTEM "file:///etc/passwd">]><transactions><transaction><txid>&x;</txid></transaction></transactions>'
    r = rich_client.post("/api/import/rich", params={"format": "xml"}, content=evil)
    assert r.status_code == 422 and "XML rejected" in r.json()["error"]["message"]
    assert count(db, TxDetails) == 0


def test_oversized_bodies_are_refused(rich_client, monkeypatch):
    monkeypatch.setattr("backend.routers.rich.MAX_BYTES", 1000)
    r = rich_client.post("/api/import/rich", params={"format": "jsonl"}, content=b"x" * 5000)
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_import_works_when_geoip_is_unavailable(loaded_client, tmp_path):
    loaded_client.app.state.geoip = GeoIP(tmp_path / "none-c.mmdb", tmp_path / "none-a.mmdb")
    tx = stored(loaded_client)[0]
    out = post(loaded_client, [rich_for(tx)]).json()
    assert out["attached"] == 1 and out["geoip_available"] is False and out["rejected"] == []
    f = loaded_client.get(f"/api/transactions/{tx['transaction_id']}/details").json()["flow"]
    assert (f["src_ip"], f["geo_country"], f["asn"]) == ("81.2.3.4", None, None)
    st = loaded_client.get("/api/geoip/status").json()
    assert st["available"] is False and "not available" in st["message"] and st["attribution"] == "IP geolocation by DB-IP.com"


def test_geoip_status_endpoint(rich_client):
    st = rich_client.get("/api/geoip/status").json()
    assert st["available"] and st["complete"] and st["license"] == "CC BY 4.0" and [d["kind"] for d in st["databases"]] == ["country", "asn"]
    assert st["databases"][0]["build_date"] == "2026-09-01" or st["databases"][0]["build_date"]              # the stub reports a build date


def test_replacing_the_synthetic_data_also_removes_the_detail_rows(rich_client):
    post(rich_client, [rich_for(t) for t in stored(rich_client)])
    r = rich_client.post("/api/import/synthetic-csv", json={"replace": True})
    assert r.status_code == 200, r.text
    db = rich_client.app.state.db
    assert count(db, TxDetails) == count(db, TxInput) == count(db, TxOutput) == count(db, FlowRecord) == 0


# ---- schema is additive ----------------------------------------------------------------------------------------------------------------
EXISTING_COLUMNS = {
    "transactions": ["transaction_id", "timestamp", "sender_wallet", "receiver_wallet", "amount_btc", "input_count", "output_count", "source", "source_ref", "created_at"],
    "network_observations": ["observation_id", "transaction_id", "wallet_address", "ip_address", "device_id", "user_agent", "network_type", "session_id", "geo_region",
                             "observed_at", "origin", "is_synthetic"],
}


def test_the_new_tables_are_additive(db):
    names = set(inspect(db.engine).get_table_names())
    # 20 at Phase 1; Phase 2 (backend/analysis/entities.py, correlation.py) additively added 5 more
    # (address_entities, address_entity_members, entity_ip_links, entity_links, correlation_findings) -> 25.
    assert {"tx_details", "tx_inputs", "tx_outputs", "flow_records"} <= names and len(names) == 25 == len(Base.metadata.tables)
    for table, columns in EXISTING_COLUMNS.items():
        assert [c["name"] for c in inspect(db.engine).get_columns(table)] == columns
    indexed = {c for t in ("tx_details", "tx_inputs", "tx_outputs", "flow_records") for ix in inspect(db.engine).get_indexes(t) for c in ix["column_names"]}
    assert {"txid", "address", "src_ip", "dst_ip", "asn"} <= indexed


def test_the_database_enforces_the_new_constraints(db):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with db.transaction() as s:
            s.add(TxDetails(transaction_id="missing", txid="a" * 64, fee_btc=0.0, script_type="p2wpkh"))      # foreign key
    with pytest.raises(IntegrityError):
        with db.transaction() as s:
            from backend.models import Wallet
            s.add_all([Wallet(address="w1", source="synthetic"), Wallet(address="w2", source="synthetic")])
            s.flush()
            s.add(Transaction(transaction_id="t1", timestamp=__import__("datetime").datetime(2026, 1, 1), sender_wallet="w1", receiver_wallet="w2", amount_btc=1.0, source="synthetic"))
            s.flush()
            s.add(TxDetails(transaction_id="t1", txid="a" * 64, fee_btc=0.0, script_type="p2xx"))              # check constraint


# ---- the flat view is reproduced exactly; nothing in the analysis reads rich data ---------------------------------------------------------
@pytest.mark.skipif(not (RICH_DIR / "synthetic_rich_transactions.jsonl").is_file(), reason="rich dataset not generated")
def test_flattening_the_rich_dataset_reproduces_the_original_transfers_exactly():
    from backend.ingestion.synthetic_csv import SyntheticCSVSource

    original = SyntheticCSVSource(PROJECT_ROOT / "dataset" / "synthetic_bitcoin_transactions.csv").fetch()
    rows = [json.loads(line) for line in (RICH_DIR / "synthetic_rich_transactions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(original) == 5000
    for r, it in zip(rows, original):
        t = it.transaction
        flat = (r["timestamp"], r["legacy_transaction_id"], r["sender_wallet"], r["receiver_wallet"], r["amount_btc"], len(r["input_addresses"]), len(r["output_addresses"]))
        assert flat == (t.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"), t.transaction_id, t.sender_wallet, t.receiver_wallet, t.amount_btc, t.input_count, t.output_count)
        assert sum(round(x * SAT) for x in r["input_amounts"]) == sum(round(x * SAT) for x in r["output_amounts"]) + round(r["fee_btc"] * SAT)
        assert round(r["amount_btc"] * SAT) in {round(x * SAT) for x in r["output_amounts"]}


@pytest.mark.skipif(not (RICH_DIR / "synthetic_rich_transactions.jsonl").is_file(), reason="rich dataset not generated")
def test_the_ground_truth_file_covers_every_address_and_is_never_read_by_the_backend():
    truth = {row["address"]: row["owner_wallet"] for row in csv.DictReader(open(RICH_DIR / "ground_truth_address_owner.csv", encoding="utf-8"))}
    rows = [json.loads(line) for line in (RICH_DIR / "synthetic_rich_transactions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(a in truth for r in rows for a in r["input_addresses"] + r["output_addresses"])
    assert all(truth[a] == r["sender_wallet"] for r in rows for a in r["input_addresses"])
    hits = [p.relative_to(PROJECT_ROOT).as_posix() for p in (PROJECT_ROOT / "backend").rglob("*.py") if "ground_truth" in p.read_text(encoding="utf-8") and "tests" not in p.parts]
    assert hits == []


def test_no_rich_field_is_read_by_the_ml_code_or_the_analysis():
    """
    Guards the ORIGINAL wallet-level pipeline (ml/ and the Phase-<=1 modules of backend/analysis/: fusion.py,
    clustering.py, service.py, ml_bridge.py, rules_meta.py) against reading Phase 1's rich/address-level data.

    Phase 2 (backend/analysis/entities.py, correlation.py) is a DELIBERATE, separate address-level analysis layer
    that legitimately reads tx_inputs/tx_outputs/tx_details/flow_records -- that is its entire purpose -- so those
    two modules are excluded here and are instead checked by their own, more precise tests in test_entities.py
    (which assert they never read sender_wallet, receiver_wallet, amount_btc, wallets, or the ground-truth-style
    address-ownership file: the actual fields that would leak the flat wallet-level view or the answer key).
    """
    forbidden = ("tx_details", "tx_inputs", "tx_outputs", "flow_records", "TxDetails", "TxInput", "TxOutput", "FlowRecord", "rich_import", "rich_formats",
                 "geoip", "src_ip", "dst_ip", "ground_truth", "script_type", "dataset/rich", "dataset\\rich")
    exclude = {"backend/analysis/entities.py", "backend/analysis/correlation.py"}
    paths = list((PROJECT_ROOT / "ml").rglob("*.py")) + list((PROJECT_ROOT / "backend" / "analysis").rglob("*.py"))
    paths = [p for p in paths if p.relative_to(PROJECT_ROOT).as_posix() not in exclude]
    assert len(paths) >= 8
    offenders = {p.relative_to(PROJECT_ROOT).as_posix(): [w for w in forbidden if w in p.read_text(encoding="utf-8")] for p in paths}
    assert {k: v for k, v in offenders.items() if v} == {}


# ---- the existing results do not move ------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(not (RICH_DIR / "synthetic_rich_transactions.jsonl").is_file(), reason="rich dataset not generated")
def test_importing_the_whole_rich_dataset_leaves_every_analysis_result_identical(real_client):
    before = real_client.get("/api/analysis/wallets").json()
    assert before["scored_wallets"] == 410 and sum(w["ml_prediction"] == "Anomalous" for w in before["wallets"]) == 41 and sum(w["is_lead"] for w in before["wallets"]) == 42
    clusters_before = real_client.get("/api/clusters", params={"limit": 500}).json()
    real_client.app.state.geoip = stub_geo()

    out = real_client.post("/api/import/rich", params={"format": "jsonl"}, content=(RICH_DIR / "synthetic_rich_transactions.jsonl").read_bytes())
    assert out.status_code == 200, out.text
    body = out.json()
    assert (body["received"], body["attached"], body["created"], body["duplicates"], body["rejected"]) == (5000, 5000, 0, 0, [])
    assert real_client.get("/api/overview").json()["transactions_total"] == 5000 and body["details_total"] == 5000

    run = real_client.post("/api/analysis/run")
    assert run.status_code == 200, run.text
    after = real_client.get("/api/analysis/wallets").json()
    assert after["scored_wallets"] == 410 and sum(w["ml_prediction"] == "Anomalous" for w in after["wallets"]) == 41 and sum(w["is_lead"] for w in after["wallets"]) == 42
    strip = lambda ws: [(w["wallet_address"], w["ml_score"], w["ml_prediction"], w["forensic_rule_count"], w["combined_score"], w["priority_rank"], w["priority_level"], w["features"]) for w in ws]
    assert strip(after["wallets"]) == strip(before["wallets"])
    clusters_after = real_client.get("/api/clusters", params={"limit": 500}).json()
    assert clusters_after["total"] == clusters_before["total"] == 52
    assert sorted(c["cluster_id"] for c in clusters_after["items"]) == sorted(c["cluster_id"] for c in clusters_before["items"])
    with open(PROJECT_ROOT / "data" / "fusion_results.csv", newline="", encoding="utf-8") as fh:
        csv_rows = {r["wallet_address"]: r for r in csv.DictReader(fh)}
    for w in after["wallets"]:
        c = csv_rows[w["wallet_address"]]
        assert w["ml_prediction"] == c["ml_anomaly_prediction"] and w["forensic_rule_count"] == int(c["forensic_rule_count"])
        assert abs(w["ml_score"] - float(c["ml_anomaly_score"])) < 1e-6 and abs(w["combined_score"] - float(c["combined_score"])) < 1e-6
