package com.acmecorp.qa.pages;

import com.acmecorp.qa.base.BasePage;
import java.util.List;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.FindBy;
import org.openqa.selenium.support.FindBys;
import org.openqa.selenium.support.How;

public class LoginPage extends BasePage {

    @FindBy(id = "username")
    private WebElement usernameInput;

    @FindBy(css = "input[data-testid='password']")
    private WebElement passwordInput;

    @FindBy(xpath = "//button[@data-qa='sign-in' and normalize-space()='Sign in to AcmeCorp']")
    public WebElement signInButton;

    @FindBy(how = How.XPATH, using = "//div[contains(@class,'welcome-banner')]//h1")
    private WebElement welcomeBanner;

    @FindBys({@FindBy(css = "form#acme-login"), @FindBy(tagName = "button")})
    private List<WebElement> formButtons;

    public LoginPage(WebDriver driver) {
        super(driver);
    }

    public LoginPage open() {
        open("/login?tenant=acmecorp");
        return this;
    }

    public LoginPage enterUsername(String name) {
        usernameInput.clear();
        usernameInput.sendKeys(name);
        return this;
    }

    public LoginPage enterPassword(String secret) {
        passwordInput.sendKeys(secret);
        return this;
    }

    public OrdersPage loginAs(String user, String secret) {
        enterUsername(user);
        enterPassword(secret);
        signInButton.click();
        return new OrdersPage(driver);
    }

    public String welcomeText() {
        return welcomeBanner.getText();
    }
}
