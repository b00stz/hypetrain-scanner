from discovery.tickers import extract_tickers, load_known_tickers, DEFAULT_TICKERS_CSV

KNOWN = {"GME", "AMC", "TSLA", "BB", "A"}


def test_extracts_cashtag():
    assert extract_tickers("$GME to the moon", KNOWN) == {"GME"}


def test_extracts_bare_uppercase_token():
    assert extract_tickers("GME calls printing", KNOWN) == {"GME"}


def test_ignores_lowercase_words_that_match_a_ticker():
    assert extract_tickers("i am bb-ing right now", KNOWN) == set()


def test_ignores_unknown_uppercase_tokens():
    assert extract_tickers("YOLO into GME", KNOWN) == {"GME"}


def test_multiple_tickers_in_one_string():
    assert extract_tickers("$GME and AMC both ripping, TSLA lagging", KNOWN) == {
        "GME", "AMC", "TSLA",
    }


def test_single_letter_ticker_as_bare_word_is_not_matched_mid_sentence():
    # "A" is a common English word too -- bare single-letter tokens are real edge cases;
    # confirm the regex only matches it when it's genuinely an isolated uppercase token.
    assert extract_tickers("a rally is coming", KNOWN) == set()
    assert extract_tickers("Rating: A upgrade today", KNOWN) == {"A"}


def test_empty_text_returns_empty_set():
    assert extract_tickers("", KNOWN) == set()
    assert extract_tickers(None, KNOWN) == set()


def test_load_known_tickers_respects_exclusions(tmp_path):
    csv_path = tmp_path / "tickers.csv"
    csv_path.write_text("ticker,name\nGME,GameStop\nAMC,AMC Entertainment\n")
    loaded = load_known_tickers(str(csv_path), excluded=["AMC"])
    assert loaded == {"GME"}


def test_default_tickers_csv_loads_and_contains_common_names():
    loaded = load_known_tickers(DEFAULT_TICKERS_CSV)
    assert "AAPL" in loaded
    assert "GME" in loaded
    assert len(loaded) > 100
