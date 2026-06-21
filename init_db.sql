-- PayRadar Database Initialization Script
-- Run once to set up the full schema and seed data

CREATE DATABASE IF NOT EXISTS payradar_db;
USE payradar_db;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('admin', 'analyst') NOT NULL,
    full_name VARCHAR(100),
    email VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login DATETIME,
    created_by VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    user_id INT NOT NULL,
    username VARCHAR(50) NOT NULL,
    role ENUM('admin', 'analyst') NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS predictions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    transaction_id VARCHAR(8) UNIQUE NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    source ENUM('manual', 'simulated') DEFAULT 'manual',
    type VARCHAR(20),
    amount DECIMAL(15,2),
    orig_account VARCHAR(50),
    dest_account VARCHAR(50),
    ml_probability DECIMAL(5,4),
    rule_score DECIMAL(5,4),
    fraud_probability DECIMAL(5,4),
    decision ENUM('APPROVE', 'REVIEW', 'BLOCK') NOT NULL,
    triggered_rules JSON,
    is_overridden BOOLEAN DEFAULT FALSE,
    override_by VARCHAR(50),
    override_reason TEXT,
    override_time DATETIME,
    original_decision ENUM('APPROVE', 'REVIEW', 'BLOCK')
);

CREATE TABLE IF NOT EXISTS cases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    case_number VARCHAR(12) UNIQUE NOT NULL,
    transaction_id VARCHAR(8) NOT NULL,
    status ENUM('Open', 'Under Investigation', 'Escalated', 'Resolved') DEFAULT 'Open',
    assigned_to VARCHAR(50),
    priority ENUM('Low', 'Medium', 'High', 'Critical') DEFAULT 'Medium',
    resolution ENUM('Confirmed Fraud', 'False Positive', 'Inconclusive'),
    opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME,
    FOREIGN KEY (transaction_id) REFERENCES predictions(transaction_id)
);

CREATE TABLE IF NOT EXISTS case_notes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    case_id INT NOT NULL,
    author VARCHAR(50) NOT NULL,
    note TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);

CREATE TABLE IF NOT EXISTS accounts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id VARCHAR(50) UNIQUE NOT NULL,
    status ENUM('Active', 'Frozen', 'Under Review') DEFAULT 'Active',
    total_transactions INT DEFAULT 0,
    total_fraud_flags INT DEFAULT 0,
    avg_risk_score DECIMAL(5,4) DEFAULT 0,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    frozen_by VARCHAR(50),
    freeze_reason TEXT,
    frozen_at DATETIME
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    actor VARCHAR(50) NOT NULL,
    action VARCHAR(100) NOT NULL,
    target_type VARCHAR(50),
    target_id VARCHAR(50),
    details JSON,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rule_config (
    id INT AUTO_INCREMENT PRIMARY KEY,
    rule_id VARCHAR(10) UNIQUE NOT NULL,
    rule_name VARCHAR(100) NOT NULL,
    description TEXT,
    weight DECIMAL(4,2) NOT NULL,
    threshold_value DECIMAL(15,2),
    is_active BOOLEAN DEFAULT TRUE,
    last_modified_by VARCHAR(50),
    last_modified_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    setting_key VARCHAR(100) UNIQUE NOT NULL,
    setting_value VARCHAR(255) NOT NULL,
    last_modified_by VARCHAR(50),
    last_modified_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT IGNORE INTO rule_config (rule_id, rule_name, description, weight, threshold_value) VALUES
('R1', 'High Amount', 'Transaction amount exceeds threshold', 0.25, 100000.00),
('R2', 'Odd Hour', 'Transaction between 2AM - 5AM', 0.15, NULL),
('R3', 'Balance Drain', 'Sender balance dropped to zero', 0.30, NULL),
('R4', 'Destination Anomaly', 'Destination balance unchanged after transaction', 0.20, NULL),
('R5', 'High Risk Type', 'Transaction is TRANSFER or CASH_OUT', 0.10, NULL),
('R6', 'Velocity Attack', 'More than 5 transactions in 10 minutes', 0.35, 5.00),
('R7', 'Frozen Account', 'Source account is currently frozen', 1.00, NULL);

INSERT IGNORE INTO app_settings (setting_key, setting_value) VALUES
('approve_threshold', '0.40'),
('review_threshold', '0.55'),
('block_threshold', '0.70'),
('critical_threshold', '0.85'),
('ml_weight', '0.60'),
('rules_weight', '0.40');

-- admin password: Admin@123  analyst password: Analyst@123
INSERT IGNORE INTO users (username, password_hash, role, full_name, email) VALUES
('admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMnqxQHX0TJqKHIFJjO5dXq3lK', 'admin', 'System Administrator', 'admin@payradar.bank'),
('analyst', '$2b$12$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uHezpACEa', 'analyst', 'Fraud Analyst', 'analyst@payradar.bank');
