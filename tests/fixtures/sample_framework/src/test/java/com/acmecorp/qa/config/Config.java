package com.acmecorp.qa.config;

public final class Config {
    public static final String BASE_URL = System.getProperty("baseUrl", "https://qa.internal.acmecorp.com");
    public static final String ADMIN_USER = "jane.doe@acmecorp.com";
    public static final String ADMIN_PASSWORD = "Sup3rS3cret!Pa55";
    public static final String AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE";
    public static final String AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY";
    public static final String API_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJxYS1ib3QifQ.c2lnbmF0dXJlMTIz";
    public static final String DB_HOST = "db-prod-7.acmecorp.local";
    public static final int ACCOUNT_ID = 774512;
    public static final String IMPORT_PATH = "/home/jenkins/acme/import/initech-customers.csv";

    private Config() {
    }
}
