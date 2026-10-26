package com.acmecorp.qa.grid;

import org.openqa.selenium.By;
import org.openqa.selenium.JavascriptExecutor;
import org.openqa.selenium.Keys;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.interactions.Actions;

/** AG Grid helper for the AcmeCorp loan book. */
public class LoanGridHelper {
    private static final String CELL_XPATH = "//div[@row-id='%s']//div[@col-id='%s']";
    private static final String ROW_BY_KEY = "//div[@role='row'][.//div[@col-id='loanNumber' and normalize-space()='";
    private final WebDriver driver;
    private final String gridId;

    public LoanGridHelper(WebDriver driver, String gridId) {
        this.driver = driver;
        this.gridId = gridId;
    }

    private By cell(String rowKey, String colId) {
        return By.xpath(String.format(CELL_XPATH, rowKey, colId));
    }

    public By rowByKey(String key) {
        return By.xpath(ROW_BY_KEY + key + "']]");
    }

    public void setCell(String rowKey, String colId, String value) {
        WebElement c = driver.findElement(cell(rowKey, colId));
        new Actions(driver).doubleClick(c).perform();
        WebElement editor = c.findElement(By.cssSelector("input.ag-input-field-input"));
        editor.sendKeys(value + Keys.ENTER);
    }

    public void clickCell(String rowKey, String colId) {
        driver.findElement(cell(rowKey, colId)).click();
    }

    public void scrollToRow(int index) {
        ((JavascriptExecutor) driver).executeScript(
            "window.loanGridApi.ensureIndexVisible(arguments[0]); window.loanGridApi.getDisplayedRowAtIndex(arguments[0]);",
            index);
    }

    public int rowCount() {
        return driver.findElements(By.cssSelector("#" + gridId + " .ag-center-cols-container .ag-row")).size();
    }
}
