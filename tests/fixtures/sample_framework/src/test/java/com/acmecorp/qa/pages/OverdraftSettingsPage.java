package com.acmecorp.qa.pages;

import com.acmecorp.qa.base.BasePage;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.FindBy;
import org.openqa.selenium.support.ui.Select;

public class OverdraftSettingsPage extends BasePage {

    @FindBy(id = "wire-transfer-amount")
    public WebElement wireTransferAmountInput;

    @FindBy(xpath = "//button[text()='Approve loan']")
    private WebElement approveLoanButton;

    @FindBy(name = "approveLoan")
    private WebElement approveLoanCheckbox;

    @FindBy(css = "select[data-qa='transfer-type']")
    private WebElement transferTypeSelect;

    @FindBy(xpath = "//*[@data-acme-id='overdraft-limit']")
    private WebElement overdraftLimitField;

    public OverdraftSettingsPage(WebDriver driver) {
        super(driver);
    }

    public void chooseTransferType(String transferType) {
        new Select(transferTypeSelect).selectByVisibleText(transferType);
    }

    public void enableApproval() {
        if (!approveLoanCheckbox.isSelected()) {
            approveLoanCheckbox.click();
        }
    }

    public void enterTransferAmount(String amount) {
        type(By.id("wire-transfer-amount"), amount);
    }
}
