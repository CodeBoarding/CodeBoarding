var builder = DistributedApplication.CreateBuilder(args);

var postgres = builder.AddPostgres("postgres");
var catalogDb = postgres.AddDatabase("catalogdb");

// A unit of this repository answers to `eventbus`, so the box is the code and this is how it runs.
var bus = builder.AddRabbitMQ("eventbus");

// A name that carries a catalogue word declares nothing; the constructor is the declaration.
var key = builder.AddParameter("openai-key", secret: true);
builder.AddAzureContainerAppEnvironment("vaultwarden");

var catalogApi = builder.AddProject<Projects.Catalog_Api>("catalog-api")
    .WithReference(catalogDb);

// No variable is kept, and it is still a gateway; the project its chain names is what it routes to.
builder.AddYarp("edge")
    .WithReference(catalogApi);

builder.Build().Run();
