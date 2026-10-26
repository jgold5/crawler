package com.globexbank.qa.pages;

import java.time.Duration;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.PageFactory;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;

/** Base page for Globex Online Banking. See GLX-1042. Contact: Hank Scorpio. */
public abstract class BasePage {
    protected final WebDriver driver;
    protected final WebDriverWait wait;
    private static final String SPINNER = "//div[contains(@class,'globex-spinner')]";

    protected BasePage(WebDriver driver) {
        this.driver = driver;
        this.wait = new WebDriverWait(driver, Duration.ofSeconds(30));
        PageFactory.initElements(driver, this);
    }

    protected WebElement waitForVisible(By by) {
        wait.until(ExpectedConditions.invisibilityOfElementLocated(By.xpath(SPINNER)));
        return wait.until(ExpectedConditions.visibilityOfElementLocated(by));
    }

    protected void clickWhenReady(By by) {
        wait.until(ExpectedConditions.elementToBeClickable(by)).click();
    }
}
