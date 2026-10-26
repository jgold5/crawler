"""Fixed vocabularies used by the redactors. Everything here is public, non-secret knowledge."""
from __future__ import annotations


def _words(s: str) -> frozenset[str]:
    return frozenset(s.split())


# Placeholders the redactors emit inside string literals / step text.
PLACEHOLDERS = _words("""
v text int number url email path xpath css secret string Name value doc p regex date json html selector name
attr param
""")
VALUE_SUBTYPES = _words("auto-id hashed-class state-class semantic-class number word kebab camel")

HTML_TAGS = _words("""
a abbr address area article aside audio b bdi bdo blockquote body br button canvas caption cite code col colgroup
data datalist dd del details dfn dialog div dl dt em embed fieldset figcaption figure footer form frame frameset
h1 h2 h3 h4 h5 h6 head header hgroup hr html i iframe img input ins kbd label legend li link main map mark menu
meta meter nav noscript object ol optgroup option output p param picture pre progress q s samp section select
slot small source span strong sub summary sup svg table tbody td template textarea tfoot th thead time title tr
track u ul var video wbr path g use rect circle polygon line
""")

XPATH_FUNCS = _words("""
last position count id local-name namespace-uri name string concat starts-with contains substring-before
substring-after substring string-length normalize-space translate boolean not true false lang number sum floor
ceiling round text node comment processing-instruction
""")
XPATH_AXES = _words("""
ancestor ancestor-or-self attribute child descendant descendant-or-self following following-sibling namespace
parent preceding preceding-sibling self
""")
XPATH_OPERATOR_NAMES = _words("and or div mod")

# Attribute names that are kept verbatim in locator shapes. Anything else becomes "data-custom".
SAFE_ATTRS = _words("""
data-testid data-test data-test-id data-qa data-cy data-automation-id role id name class type placeholder col-id
row-id row-index title href value for alt src label disabled checked selected readonly tabindex target rel lang
dir hidden multiple required maxlength autocomplete action method scope colspan rowspan form style
""")
TEST_ID_ATTRS = _words("data-testid data-test data-test-id data-qa data-cy data-automation-id")

CSS_PSEUDOS = _words("""
first-child last-child nth-child nth-last-child nth-of-type nth-last-of-type first-of-type last-of-type
only-child only-of-type not has is where hover focus focus-within active checked disabled enabled empty visible
before after root required optional read-only placeholder-shown selected contains
""")

# Common English / UI / testing words. Capitalized words outside this list become <Name> in step phrases.
SAFE_WORDS = _words("""
a about above access accept accepted account accounts action actions active add added address admin after again
alert all allow allowed already also amount an and any app appear appears application apply approve approved are
area as ask at attach attached attribute available back background bar base be been before below between blank
both box browser but button buttons by calendar can cancel cancelled cannot card cart cell cells change changed
check checkbox checked checkout choose clear click clicked close closed code collapse column columns comment
confirm confirmation contain contains content continue copy correct count create created current customer
customers dashboard data date day days default delete deleted description details dialog disabled display
displayed do does done down download drag drop dropdown each edit edited editor element elements email empty
enabled end enter entered environment equal error errors every exist exists expand expected export failed
field fields file filter filtered find first following for form from full get given go goes grid group has have
header heading help hidden hide home hover i id if import in info information input invalid is it item items
its key label language last leave left less line link list load loaded loading log logged login logout make
manager max menu message messages min missing modal more move must my name navigate new next no not note
notification number of off ok on one only open opened option options or order orders other out page pages
pagination panel password pending per pick place please popup present press previous price product products
profile quantity record records refresh reject rejected reload remove removed report reports required reset
result results return right role row rows save saved scenario screen scroll search second section see select
selected send set settings should show shown sign size skip sort sorted start status step submit submitted
success successful successfully summary switch tab table tabs text that the then there these this time title to
toast toggle top total true type uncheck under unique up update updated upload url user username users valid
value values verify view visible wait warning when where which will window with within without yes you your
""")

# --- identifier anonymization -------------------------------------------------------------------------------

ROLE_WORDS = _words("""
page pages po steps step stepdefs defs definitions definition glue helper helpers utils util base component
components modal dialog grid table row rows cell cells column columns col header input field fields button btn
link dropdown select checkbox radio toggle tab menu item items list label text icon locator locators by element
elements driver wait waits factory config hooks hook runner context world api client test tests object section
panel form view screen manager service provider builder container widget popup
""")
CLASSY_ROLES = ROLE_WORDS  # roles that may end a class name

VERBS = _words("""
click enter type fill select check uncheck open go to goto navigate wait for get set is has verify assert should
scroll hover drag upload download login logout log in out search sort filter edit save cancel delete submit
expand collapse find clear press close add remove update create read init build switch accept dismiss focus
double right choose toggle are can show hide refresh reload until on do make apply reset confirm load sign
""")
FILL_VERBS = _words("enter type fill set select check uncheck upload search filter sort edit choose")
CLICK_VERBS = _words("""
click open go goto navigate submit save cancel delete expand collapse hover drag scroll download login logout
log sign close press confirm apply reset add remove create
""")

GENERIC_STEMS = _words("""
username user password email login logout sign in up out search submit cancel save close ok yes no next previous
prev back home main nav navigation header footer sidebar error errors message messages title spinner loader
loading overlay first last all new current default common generic custom value values name id xpath css url
timeout seconds millis duration visible clickable present displayed enabled disabled selected ready loaded and
or of to for on at the a an with from count size index key keys filter sort page row cell column grid table data
status result results total action actions confirm confirmation notification toast alert popup modal dialog form
details summary settings profile dashboard logo language locale date time start end min max screenshot browser
window frame iframe tab scenario hooks env environment base instance attempts retry poll polling interval element
locator string number option options dropdown file path text label link button input field list item items
abstract driver wait page web ui by js script java util helper factory config element type remote local headless
chrome firefox edge safari grid hub capabilities options properties property is has get set click enter fill
""")

PACKAGE_GENERIC = _words("""
com org net io de uk co qa test tests testing automation framework ui e2e main java src app web pages page steps
step stepdefs stepdefinitions definitions glue utils util helpers helper base core common config hooks runner
runners components component grid grids support driver drivers factory context api models model data constants
features tools tool lib libs internal shared selenium cucumber bdd acceptance functional regression
""")

# Third-party package prefixes whose imports are kept verbatim (conventions, not secrets).
SAFE_IMPORT_PREFIXES = (
    "java.", "javax.", "jakarta.", "org.openqa.selenium", "io.cucumber", "cucumber.api", "org.junit", "junit.",
    "org.testng", "org.hamcrest", "org.assertj", "io.github.bonigarcia", "org.slf4j", "org.apache.logging.log4j",
    "org.apache.commons", "com.fasterxml.jackson", "lombok", "io.restassured", "org.awaitility",
    "com.google.common", "com.google.gson", "io.qameta.allure", "net.serenitybdd", "net.thucydides",
    "org.springframework", "com.codeborne.selenide", "com.aventstack", "io.appium", "org.json", "okhttp3",
    "dev.failsafe", "net.datafaker", "com.github.javafaker", "org.picocontainer", "com.google.inject",
)

JAVA_LANG_TYPES = _words("""
String Integer Long Short Byte Double Float Boolean Character Object Number Math System Thread Runnable Exception
RuntimeException Error Throwable IllegalArgumentException IllegalStateException UnsupportedOperationException
NullPointerException InterruptedException StringBuilder StringBuffer Class Enum Iterable Void Override Deprecated
SuppressWarnings FunctionalInterface SafeVarargs CharSequence Comparable AutoCloseable Record
""")

# Methods/fields of well-known APIs (JDK, Selenium, Cucumber, JUnit/TestNG/AssertJ) kept verbatim.
KNOWN_API_NAMES = _words("""
findElement findElements sendKeys click clear getText getAttribute getDomAttribute getDomProperty getCssValue
getTagName isDisplayed isEnabled isSelected submit until withTimeout pollingEvery ignoring withMessage
visibilityOfElementLocated visibilityOf elementToBeClickable presenceOfElementLocated invisibilityOfElementLocated
invisibilityOf textToBePresentInElement textToBePresentInElementLocated urlContains urlToBe titleIs titleContains
numberOfElementsToBe presenceOfAllElementsLocatedBy visibilityOfAllElementsLocatedBy stalenessOf attributeContains
attributeToBe frameToBeAvailableAndSwitchToIt alertIsPresent elementSelectionStateToBe and or not refreshed
get navigate to back forward refresh quit close manage timeouts implicitlyWait pageLoadTimeout scriptTimeout window
maximize setSize getWindowHandle getWindowHandles switchTo frame defaultContent alert accept dismiss
executeScript executeAsyncScript getScreenshotAs getCurrentUrl getTitle getPageSource moveToElement perform build
doubleClick contextClick dragAndDrop keyDown keyUp release clickAndHold scrollToElement scrollByAmount pause
selectByVisibleText selectByValue selectByIndex deselectAll getOptions getFirstSelectedOption
getAllSelectedOptions isMultiple xpath cssSelector id name className linkText partialLinkText tagName
initElements ofSeconds ofMillis ofMinutes addArguments setHeadless setCapability addPreference
setExperimentalOption Given When Then And But Before After BeforeStep AfterStep BeforeAll AfterAll ParameterType
DataTableType DocStringType asMaps asMap asList asLists cells row column height width isFailed getName getStatus
attach log embed assertEquals assertNotEquals assertTrue assertFalse assertNull assertNotNull assertThat
assertThrows fail isEqualTo isNotEqualTo isTrue isFalse isNull isNotNull contains containsExactly hasSize
isEmpty isNotEmpty startsWith endsWith equals equalsIgnoreCase hashCode toString format valueOf parseInt
parseLong parseDouble length trim strip split replace replaceAll toLowerCase toUpperCase isBlank substring
indexOf charAt matches sleep currentThread println printf print getProperty getenv currentTimeMillis nanoTime
stream map filter collect forEach toList size add put remove of asList empty isPresent orElse orElseThrow ifPresent
getMessage getCause printStackTrace info debug warn error trace getLogger getClass getSimpleName iterator next
hasNext keySet values entrySet getKey getValue containsKey join builder now plusDays minusDays
XPATH CSS ID NAME CLASS_NAME LINK_TEXT PARTIAL_LINK_TEXT TAG_NAME ENTER TAB ESCAPE RETURN BACK_SPACE DELETE
ARROW_DOWN ARROW_UP CONTROL SHIFT FILE BYTES BASE64 HOW USING out err length class this super
""")
