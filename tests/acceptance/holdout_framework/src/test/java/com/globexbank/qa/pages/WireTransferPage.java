package com.globexbank.qa.pages;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;

public class WireTransferPage extends BasePage {
    public static final By WIRE_AMOUNT_INPUT = By.xpath("//input[@aria-label='Wire amount (USD)']");
    public static final By BENEFICIARY_DROPDOWN = By.cssSelector("select#beneficiary-acct-7731");
    private static final String OVERDRAFT_XPATH = """
        //section[h2[normalize-space()='Overdraft Protection Opt-In']]
          //input[@type='checkbox']
        """;

    public record Beneficiary(String name, String routingNumber) {}

    public WireTransferPage(WebDriver driver) { super(driver); }

    public void enterWireAmount(String amount) {
        var field = waitForVisible(WIRE_AMOUNT_INPUT);
        field.clear();
        field.sendKeys(amount);
    }

    public String limitFor(String tier) {
        return switch (tier) {
            case "PLATINUM" -> "250000";
            case "GOLD" -> "50000";
            default -> "10000";
        };
    }
}
