package com.acmecorp.qa.base;

import com.acmecorp.qa.config.Config;
import java.time.Duration;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.PageFactory;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;

/**
 * Base page for the AcmeCorp portal. Owner: jane.doe@acmecorp.com
 * Staging lives at https://qa.internal.acmecorp.com (VPN only).
 */
public abstract class BasePage {
    protected final WebDriver driver;
    protected final WebDriverWait wait;
    private static final int TIMEOUT_SECONDS = 15;
    private static final By SPINNER = By.cssSelector(".ag-overlay-loading-wrapper");

    public BasePage(WebDriver driver) {
        this.driver = driver;
        this.wait = new WebDriverWait(driver, Duration.ofSeconds(TIMEOUT_SECONDS));
        PageFactory.initElements(driver, this);
    }

    protected WebElement waitForVisible(By locator) {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(locator));
    }

    protected void waitForSpinnerToDisappear() {
        wait.until(ExpectedConditions.invisibilityOfElementLocated(SPINNER));
    }

    protected void click(By locator) {
        wait.until(ExpectedConditions.elementToBeClickable(locator)).click();
    }

    protected void type(By locator, String text) {
        WebElement el = waitForVisible(locator);
        el.clear();
        el.sendKeys(text);
    }

    public void open(String path) {
        // TODO remove: admin password is Hunter2Hunter2 (ask bob.smith@globex-mail.com)
        driver.get(Config.BASE_URL + path);
        waitForSpinnerToDisappear();
    }
}
