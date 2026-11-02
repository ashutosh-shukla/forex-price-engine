-- =====================================================================
-- Price Engine: Oracle Autonomous Database schema
-- Run in: Oracle Cloud console -> your Autonomous Database -> Database actions
--         -> SQL, signed in as the same user your engine connects with
--         (the ORACLE_ADB_USER in .env). Use "Run Script" (F5), not "Run Statement".
-- Safe to run more than once: objects that already exist are skipped.
-- All tables start with PE_ so they never clash with your other tables.
-- =====================================================================


-- 1) trading pairs the engine streams

BEGIN
    EXECUTE IMMEDIATE 'CREATE TABLE pe_pairs (
        symbol          VARCHAR2(20)  NOT NULL,
        display_name    VARCHAR2(40),
        price_decimals  NUMBER(2)     DEFAULT 5 NOT NULL,
        active          NUMBER(1)     DEFAULT 1 NOT NULL,
        created_at      TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
        CONSTRAINT pe_pairs_pk PRIMARY KEY (symbol)
)';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- 2) API clients (one per project that consumes the engine). Keys are stored hashed only.

BEGIN
    EXECUTE IMMEDIATE 'CREATE TABLE pe_clients (
        client_id             VARCHAR2(32)  NOT NULL,
        name                  VARCHAR2(80)  NOT NULL,
        key_hash              VARCHAR2(64)  NOT NULL,
        allowed_symbols       VARCHAR2(2000),
        allowed_timeframes    VARCHAR2(500),
        allow_metrics         NUMBER(1)     DEFAULT 1 NOT NULL,
        allow_ws              NUMBER(1)     DEFAULT 1 NOT NULL,
        allow_ticks           NUMBER(1)     DEFAULT 0 NOT NULL,
        rate_limit_per_min    NUMBER(10)    DEFAULT 6000 NOT NULL,
        max_ws_connections    NUMBER(10)    DEFAULT 5000 NOT NULL,
        allowed_ips           VARCHAR2(2000),
        ws_token_ttl_seconds  NUMBER(5)     DEFAULT 60 NOT NULL,
        active                NUMBER(1)     DEFAULT 1 NOT NULL,
        created_at            TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
        last_used_at          TIMESTAMP,
        CONSTRAINT pe_clients_pk PRIMARY KEY (client_id)
)';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- 3) 1-minute candles with order-flow metrics (every other timeframe is built from these)
--    ts_ms = candle start time in epoch milliseconds (UTC)

BEGIN
    EXECUTE IMMEDIATE 'CREATE TABLE pe_candles_1m (
        symbol VARCHAR2(20) NOT NULL,
        ts_ms NUMBER(16) NOT NULL,
        open_px BINARY_DOUBLE NOT NULL,
        high_px BINARY_DOUBLE NOT NULL,
        low_px BINARY_DOUBLE NOT NULL,
        close_px BINARY_DOUBLE NOT NULL,
        tick_count NUMBER(10),
        up_ticks NUMBER(10),
        down_ticks NUMBER(10),
        move_up BINARY_DOUBLE,
        move_down BINARY_DOUBLE,
        path_length BINARY_DOUBLE,
        reversals NUMBER(10),
        max_run_up NUMBER(10),
        max_run_down NUMBER(10),
        max_jump_up BINARY_DOUBLE,
        max_jump_down BINARY_DOUBLE,
        spread_sum BINARY_DOUBLE,
        min_spread BINARY_DOUBLE,
        max_spread BINARY_DOUBLE,
        mid_sum BINARY_DOUBLE,
        sum_sq_move BINARY_DOUBLE,
        start_mid BINARY_DOUBLE,
        bid_close BINARY_DOUBLE,
        ask_close BINARY_DOUBLE,
        source VARCHAR2(10) DEFAULT ''live'' NOT NULL,
        CONSTRAINT pe_candles_1m_pk PRIMARY KEY (symbol, ts_ms)
)';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- 4) raw ticks (only filled when STORE_TICKS=true in .env)

BEGIN
    EXECUTE IMMEDIATE 'CREATE TABLE pe_ticks (
        symbol  VARCHAR2(20)   NOT NULL,
        ts_ms   NUMBER(16)    NOT NULL,
        bid     BINARY_DOUBLE NOT NULL,
        ask     BINARY_DOUBLE NOT NULL
)';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
    EXECUTE IMMEDIATE 'CREATE INDEX pe_ticks_sym_ts_ix ON pe_ticks (symbol, ts_ms)';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 AND SQLCODE != -1408 THEN RAISE; END IF;
END;
/

BEGIN
    EXECUTE IMMEDIATE 'CREATE INDEX pe_ticks_ts_ix ON pe_ticks (ts_ms)';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 AND SQLCODE != -1408 THEN RAISE; END IF;
END;
/

-- check afterwards: you should see 7 tables
-- SELECT table_name FROM user_tables WHERE table_name LIKE 'PE\_%' ESCAPE '\';

-- =====================================================================
-- Analysis screen tables (manual analysis runs, one row per closed candle, notes)
-- =====================================================================

-- 6) one row per closed candle captured while analysis is ON; OANDA-official values, outcomes and your labels fill in later

BEGIN
    EXECUTE IMMEDIATE 'CREATE TABLE pe_analysis (
        symbol VARCHAR2(20) NOT NULL,
        tf VARCHAR2(8) NOT NULL,
        ts_ms NUMBER(16) NOT NULL,
        run_id NUMBER(16),
        sessions VARCHAR2(120),
        partial NUMBER(1),
        captured_ms NUMBER(16),
        open_px BINARY_DOUBLE,
        high_px BINARY_DOUBLE,
        low_px BINARY_DOUBLE,
        close_px BINARY_DOUBLE,
        rng BINARY_DOUBLE,
        tick_count NUMBER(10),
        direction VARCHAR2(8),
        body_ratio BINARY_DOUBLE,
        net_move BINARY_DOUBLE,
        start_mid BINARY_DOUBLE,
        momentum BINARY_DOUBLE,
        ret_pct BINARY_DOUBLE,
        logret_pct BINARY_DOUBLE,
        momentum_accel BINARY_DOUBLE,
        max_jump BINARY_DOUBLE,
        jump_share BINARY_DOUBLE,
        max_run_up NUMBER(10),
        max_run_down NUMBER(10),
        max_pullback_high BINARY_DOUBLE,
        max_bounce_low BINARY_DOUBLE,
        up_ticks NUMBER(10),
        down_ticks NUMBER(10),
        imbalance BINARY_DOUBLE,
        move_imbalance BINARY_DOUBLE,
        move_up BINARY_DOUBLE,
        move_down BINARY_DOUBLE,
        path_length BINARY_DOUBLE,
        reversals NUMBER(10),
        rv BINARY_DOUBLE,
        rv_pct BINARY_DOUBLE,
        tick_rate BINARY_DOUBLE,
        avg_spread BINARY_DOUBLE,
        min_spread BINARY_DOUBLE,
        max_spread BINARY_DOUBLE,
        ret_5s BINARY_DOUBLE,
        mom_5s BINARY_DOUBLE,
        rng_5s BINARY_DOUBLE,
        acc_5s BINARY_DOUBLE,
        ret_15s BINARY_DOUBLE,
        mom_15s BINARY_DOUBLE,
        rng_15s BINARY_DOUBLE,
        acc_15s BINARY_DOUBLE,
        ret_30s BINARY_DOUBLE,
        mom_30s BINARY_DOUBLE,
        rng_30s BINARY_DOUBLE,
        acc_30s BINARY_DOUBLE,
        ret_60s BINARY_DOUBLE,
        mom_60s BINARY_DOUBLE,
        rng_60s BINARY_DOUBLE,
        acc_60s BINARY_DOUBLE,
        ret_z BINARY_DOUBLE,
        range_z BINARY_DOUBLE,
        ticks_z BINARY_DOUBLE,
        off_open BINARY_DOUBLE,
        off_high BINARY_DOUBLE,
        off_low BINARY_DOUBLE,
        off_close BINARY_DOUBLE,
        off_volume NUMBER(10),
        off_spread_close BINARY_DOUBLE,
        coverage BINARY_DOUBLE,
        next_dir VARCHAR2(8),
        next_ret_pct BINARY_DOUBLE,
        next_range BINARY_DOUBLE,
        next_body_ratio BINARY_DOUBLE,
        ret_5m BINARY_DOUBLE,
        ret_15m BINARY_DOUBLE,
        ret_60m BINARY_DOUBLE,
        mfe_5m BINARY_DOUBLE,
        mae_5m BINARY_DOUBLE,
        mfe_15m BINARY_DOUBLE,
        mae_15m BINARY_DOUBLE,
        mfe_60m BINARY_DOUBLE,
        mae_60m BINARY_DOUBLE,
        algo_direction VARCHAR2(8),
        actual_direction VARCHAR2(8),
        label_15m VARCHAR2(20),
        note VARCHAR2(1000),
        updated_ms NUMBER(16),
        CONSTRAINT pe_analysis_pk PRIMARY KEY (symbol, tf, ts_ms)
)';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

BEGIN
    EXECUTE IMMEDIATE 'CREATE INDEX pe_analysis_run_ix ON pe_analysis (run_id)';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 AND SQLCODE != -1408 THEN RAISE; END IF;
END;
/

-- 7) each press of Start analysis creates one run

BEGIN
    EXECUTE IMMEDIATE 'CREATE TABLE pe_analysis_runs (
        run_id      NUMBER(16)    NOT NULL,
        symbol      VARCHAR2(20)  NOT NULL,
        tfs         VARCHAR2(100),
        started_ms  NUMBER(16)    NOT NULL,
        stopped_ms  NUMBER(16),
        CONSTRAINT pe_analysis_runs_pk PRIMARY KEY (run_id)
)';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/

-- 8) free-text notes with a time (IST in the UI)

BEGIN
    EXECUTE IMMEDIATE 'CREATE TABLE pe_notes (
        note_id     NUMBER(20)     NOT NULL,
        symbol      VARCHAR2(20),
        ts_ms       NUMBER(16)     NOT NULL,
        run_id      NUMBER(16),
        note_text   VARCHAR2(2000) NOT NULL,
        CONSTRAINT pe_notes_pk PRIMARY KEY (note_id)
)';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -955 THEN RAISE; END IF;
END;
/
