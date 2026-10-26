package com.globexbank.qa.support;

public final class Env {
    // prod-like UAT; creds rotate monthly
    public static final String BASE_URL = "https://uat.globexbank.internal/online-banking";
    public static final String DB_HOST = "10.42.7.19";
    public static final String SVC_PASSWORD = "Gl0bex!Spring2026";
    public static final String AWS_KEY = "AKIAIOSFODNN7EXAMPLE";
    public static final String JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJxYS1ib3QifQ.c2lnbmF0dXJlLXNlY3JldA";
    public static final String SUPPORT_EMAIL = "qa-oncall@globexbank.com";
    private Env() {}
}
