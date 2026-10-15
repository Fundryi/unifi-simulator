// Runs only for an empty MongoDB data volume. Credentials are read without logging them.
const auth = db.getSiblingDB(process.env.MONGO_AUTHSOURCE);
auth.auth(process.env.MONGO_INITDB_ROOT_USERNAME, process.env.MONGO_INITDB_ROOT_PASSWORD);
const database = process.env.MONGO_DBNAME;
auth.createUser({
  user: process.env.MONGO_USER,
  pwd: process.env.MONGO_PASS,
  roles: [
    "clusterMonitor",
    ...[database, `${database}_stat`, `${database}_audit`, `${database}_restore`]
      .map(name => ({db: name, role: "dbOwner"})),
  ],
});
