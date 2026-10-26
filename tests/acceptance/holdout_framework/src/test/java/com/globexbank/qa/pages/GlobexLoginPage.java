package com.globexbank.qa.pages;

import lombok.Getter;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.FindBy;

@Getter
public class GlobexLoginPage extends BasePage {
    @FindBy(xpath = "//input[@data-qa='globex-username']")
    private WebElement usernameInput;

    @FindBy(id = "pwd-field")
    private WebElement passwordInput;

    @FindBy(xpath = "//button[normalize-space()='Sign in to Globex']")
    private WebElement signInButton;

    public GlobexLoginPage(WebDriver driver) { super(driver); }

    public void loginAs(String user, String pass) {
        usernameInput.sendKeys(user);
        passwordInput.sendKeys(pass);
        signInButton.click();
    }
}
