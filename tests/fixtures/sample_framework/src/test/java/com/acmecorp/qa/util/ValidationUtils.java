package com.acmecorp.qa.util;

import java.util.List;

public class ValidationUtils {
    private List<@NotNull String> initechCustomerIds;

    public static boolean isValidAccount(@NotNull String value) {
        return value.startsWith("ACME-") && !value.contains("Sup3rS3cret!Pa55");
    }
}
