import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach,it,expect,vi } from "vitest";
import { Workflow } from "./Workflow";
import { api,post } from "../api";
vi.mock("../api",()=>({api:vi.fn(),post:vi.fn()}));
beforeEach(()=>{vi.resetAllMocks();vi.mocked(api).mockImplementation(async(path)=>{
 if(path==="/model-registry")return {models:[{id:"qwen3.8:27b",local:true,available:true},{id:"qwen3:8b",local:true,available:true},{id:"gpt-6-luna",local:false,available:false}]};
 if(path==="/bootstrap")return {settings:[]};if(path==="/workspaces/w")return {files:[{id:"f",name:"Synthetic.xlsx"}]};if(path.startsWith("/workflows?"))return [];return {};
});});
it("defaults to local single, exposes exact cloud NOT RUN and never dispatches on selection",async()=>{
 render(<Workflow workspaceId="w" onError={vi.fn()}/>);await screen.findByRole("option",{name:/CLOUD NOT RUN/});
 expect(screen.getByRole("combobox",{name:"Execution mode"})).toHaveValue("single");
 expect(screen.getByRole("combobox",{name:"analyst model"})).toHaveValue("qwen3.8:27b");
 await userEvent.selectOptions(screen.getByRole("combobox",{name:"analyst model"}),"gpt-6-luna");expect(post).not.toHaveBeenCalled();
});
it("allows local three-role assignments and comparison without implying independent foundations",async()=>{
 render(<Workflow workspaceId="w" onError={vi.fn()}/>);await screen.findByRole("option",{name:/CLOUD NOT RUN/});
 await userEvent.selectOptions(screen.getByRole("combobox",{name:"Execution mode"}),"multi");
 for(const role of ["planner","analyst","reviewer"])expect(screen.getByRole("combobox",{name:`${role} model`})).toHaveValue("qwen3.8:27b");
 expect(screen.getByText(/one foundation model/)).toBeInTheDocument();
 await userEvent.selectOptions(screen.getByRole("combobox",{name:"Execution mode"}),"comparison");
 expect(screen.getByRole("combobox",{name:"a model"})).toBeInTheDocument();expect(screen.getByRole("combobox",{name:"b model"})).toBeInTheDocument();expect(post).not.toHaveBeenCalled();
});
it("restores saved mode and roles; persisted results are viewed without inference",async()=>{
 vi.mocked(api).mockImplementation(async(path)=>path==="/model-registry"?{models:[{id:"qwen3.8:27b",local:true,available:true}]}:path==="/bootstrap"?{settings:[{id:"ui",execution_mode:"multi",workflow_models:{planner:"qwen3.8:27b",analyst:"qwen3.8:27b",reviewer:"qwen3.8:27b"}}]}:path==="/workspaces/w"?{files:[]}:path.startsWith("/workflows?")?[{id:"saved",status:"BLOCKED",configuration:{mode:"multi"},created_at:"2026-10-18",result:{status:"BLOCKED",model:"qwen3.8:27b",validation:{status:"BLOCKED"},api_usd:"0"}}]:{});
 render(<Workflow workspaceId="w" onError={vi.fn()}/>);await waitFor(()=>expect(screen.getByRole("combobox",{name:"Execution mode"})).toHaveValue("multi"));
 await userEvent.selectOptions(screen.getByRole("combobox",{name:"Saved V3 workflow"}),"saved");expect(screen.getByRole("status")).toHaveTextContent("BLOCKED");expect(post).not.toHaveBeenCalled();
});
