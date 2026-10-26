package com.acmecorp.qa.models;

/* Java 17: javalang can't parse records, text blocks or switch expressions. */
public record CustomerRecord(String name, String email) {
    public static final String ROW_XPATH = """
        //div[@role='row'][.//div[@col-id='customerName' and text()='Globex Corporation']]
        """;

    public String label() {
        return switch (name.length()) {
            case 0 -> "none";
            default -> name + " <" + email + "@acmecorp.com>";
        };
    }
}
