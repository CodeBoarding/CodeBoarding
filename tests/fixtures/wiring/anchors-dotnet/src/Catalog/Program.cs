var identity = builder.Configuration["IdentityUrl"];
var bus = builder.Configuration.GetConnectionString("EventBus");
var catalog = Environment.GetEnvironmentVariable("CATALOG_MODE");
