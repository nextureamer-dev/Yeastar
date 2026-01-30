-- Yeastar CRM Database Initialization Script
-- Run this script to create the database and tables

-- Create database
CREATE DATABASE IF NOT EXISTS yeastar_crm CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE yeastar_crm;

-- Contacts table
CREATE TABLE IF NOT EXISTS contacts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100),
    company VARCHAR(200),
    email VARCHAR(255),
    phone VARCHAR(50) NOT NULL,
    phone_secondary VARCHAR(50),
    address TEXT,
    notes TEXT,
    tags VARCHAR(500),
    is_favorite BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_phone (phone),
    INDEX idx_phone_secondary (phone_secondary)
) ENGINE=InnoDB;

-- Extensions table
CREATE TABLE IF NOT EXISTS extensions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    extension_number VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(200),
    email VARCHAR(255),
    status ENUM('available', 'on_call', 'ringing', 'busy', 'dnd', 'offline') DEFAULT 'offline',
    is_registered BOOLEAN DEFAULT FALSE,
    current_call_id VARCHAR(100),
    current_caller VARCHAR(100),
    last_seen TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_extension_number (extension_number)
) ENGINE=InnoDB;

-- Call logs table
CREATE TABLE IF NOT EXISTS call_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    call_id VARCHAR(100) UNIQUE,
    contact_id INT,
    caller_number VARCHAR(50) NOT NULL,
    callee_number VARCHAR(50) NOT NULL,
    caller_name VARCHAR(200),
    callee_name VARCHAR(200),
    direction ENUM('inbound', 'outbound', 'internal') NOT NULL,
    status ENUM('answered', 'missed', 'busy', 'failed', 'no_answer') NOT NULL,
    extension VARCHAR(20),
    trunk VARCHAR(100),
    start_time TIMESTAMP NOT NULL,
    answer_time TIMESTAMP NULL,
    end_time TIMESTAMP NULL,
    duration INT DEFAULT 0,
    ring_duration INT DEFAULT 0,
    recording_file VARCHAR(500),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_call_id (call_id),
    INDEX idx_caller_number (caller_number),
    INDEX idx_callee_number (callee_number),
    INDEX idx_contact_id (contact_id),
    INDEX idx_start_time (start_time),
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Notes table
CREATE TABLE IF NOT EXISTS notes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    contact_id INT NOT NULL,
    call_log_id INT,
    content TEXT NOT NULL,
    created_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE,
    FOREIGN KEY (call_log_id) REFERENCES call_logs(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(255) UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(200),
    extension VARCHAR(20),
    is_active BOOLEAN DEFAULT TRUE,
    is_admin BOOLEAN DEFAULT FALSE,
    is_superadmin BOOLEAN DEFAULT FALSE,
    role VARCHAR(20) DEFAULT 'employee',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NULL ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_username (username),
    INDEX idx_email (email),
    INDEX idx_extension (extension)
) ENGINE=InnoDB;

-- Insert sample data (optional)
INSERT INTO contacts (first_name, last_name, company, phone, email, is_favorite) VALUES
('John', 'Doe', 'Acme Corp', '+1234567890', 'john@acme.com', TRUE),
('Jane', 'Smith', 'Tech Solutions', '+1987654321', 'jane@techsol.com', FALSE),
('Bob', 'Wilson', 'Global Inc', '+1555123456', 'bob@global.com', FALSE);

-- Grant privileges (adjust user/password as needed)
-- GRANT ALL PRIVILEGES ON yeastar_crm.* TO 'crm_user'@'localhost' IDENTIFIED BY 'your_password';
-- FLUSH PRIVILEGES;
