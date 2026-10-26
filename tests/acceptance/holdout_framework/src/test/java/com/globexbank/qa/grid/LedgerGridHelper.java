package com.globexbank.qa.grid;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.JavascriptExecutor;

// Ledger grid helper (AG Grid). Ask Marge in Risk Ops before changing.
public class LedgerGridHelper {
    private final WebDriver driver;
    public LedgerGridHelper(WebDriver driver) { this.driver = driver; }

    public By cell(String accountNumber, String colId) {
        return By.xpath(String.format("//div[@role='row'][.//div[@col-id='acctNo' and text()='%s']]//div[@col-id='%s']", accountNumber, colId));
    }

    public void scrollToRow(int idx) {
        ((JavascriptExecutor) driver).executeScript("window.globexLedgerApi.ensureIndexVisible(arguments[0])", idx);
    }
}
